"""Crisp vector widgets for the window: icon buttons and the glowing connection light.

Everything is drawn with Canvas primitives (not bitmaps), so it stays sharp on Retina displays
and follows light/dark mode.
"""
import math
import os
import tkinter as tk
from tkinter import font as tkfont

from . import theme

BLUE, RED, GREEN = "#2e86ab", "#d1495b", "#34c759"


def _rgb(widget, color):
    r, g, b = widget.winfo_rgb(color)
    return r >> 8, g >> 8, b >> 8


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _mix(a, b, t):
    """Blend colour a toward b by t (0..1)."""
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def is_dark(_widget=None):
    return theme.is_dark()


def round_rect(c, x1, y1, x2, y2, r, fill="", outline="", width=1, tags=()):
    """Rounded rectangle built from strokes.

    On macOS, Tk smooths strokes (lines, arcs, round caps and round joins) but draws filled ovals and
    polygons with coarse edges, and bitmaps are scaled up without smoothing on Retina screens. So the
    shape is a thick line along an inset rectangle: its round joins ARE the rounded corners."""
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    bw = max(1.0, float(width)) if outline else 0.0

    def band(colour, radius):
        """Fill the rounded rectangle whose corner radius is `radius`, inset by (r - radius) from the box."""
        if radius <= 0.5:
            c.create_rectangle(x1 + (r - radius), y1 + (r - radius), x2 - (r - radius), y2 - (r - radius),
                               fill=colour, outline="", tags=tags)
            return
        ax1, ay1, ax2, ay2 = x1 + r, y1 + r, x2 - r, y2 - r      # path rectangle (same for every ring)
        if ax2 - ax1 > 0.5 and ay2 - ay1 > 0.5:                   # centre, in case the stroke leaves a hole
            c.create_rectangle(ax1, ay1, ax2, ay2, fill=colour, outline="", tags=tags)
        c.create_line(ax1, ay1, ax2, ay1, ax2, ay2, ax1, ay2, ax1, ay1, ax2, ay1, fill=colour, width=2 * radius,
                      joinstyle="round", capstyle="round", tags=tags)

    if outline:
        band(outline, r)
    if fill:
        band(fill, r - bw)


def disc(c, cx, cy, radius, fill, tags=()):
    """A smooth filled circle: a zero-length line with round caps."""
    return c.create_line(cx, cy, cx + 0.01, cy, fill=fill, width=2 * radius, capstyle="round", tags=tags)


# ---- icons: draw(canvas, cx, cy, color) in an ~18px box; color=None uses each icon's own palette ----
def icon_refresh(c, cx, cy, col):
    col = col or BLUE
    r = 6.5
    c.create_arc(cx - r, cy - r, cx + r, cy + r, start=60, extent=290, style="arc", outline=col, width=2)
    th = math.radians(350)                        # end of the arc
    px, py = cx + r * math.cos(th), cy - r * math.sin(th)
    dx, dy = -math.sin(th), -math.cos(th)          # direction of travel (counter-clockwise)
    nx, ny = -dy, dx
    tip = (px + dx * 5, py + dy * 5)
    c.create_polygon([tip, (px - dx * 1 + nx * 4, py - dy * 1 + ny * 4), (px - dx * 1 - nx * 4, py - dy * 1 - ny * 4)],
                     fill=col, outline=col)


def icon_download(c, cx, cy, col):
    col = col or BLUE
    c.create_line(cx, cy - 8, cx, cy + 1, fill=col, width=2, capstyle="round")
    c.create_polygon([(cx - 5.5, cy - 1), (cx + 5.5, cy - 1), (cx, cy + 5.5)], fill=col, outline=col)
    c.create_line(cx - 7.5, cy + 3, cx - 7.5, cy + 8, cx + 7.5, cy + 8, cx + 7.5, cy + 3, fill=col, width=2,
                  joinstyle="round", capstyle="round")


def icon_open(c, cx, cy, col):
    back = col or "#7a7a7a"
    front = col or BLUE
    c.create_polygon([cx - 8, cy - 6, cx - 3, cy - 6, cx - 1, cy - 4, cx + 7, cy - 4, cx + 7, cy + 7, cx - 8, cy + 7],
                     fill="", outline=back, width=1.6, joinstyle="round")
    c.create_polygon([cx - 8, cy + 7, cx - 5, cy - 1, cx + 9, cy - 1, cx + 6, cy + 7], fill=front, outline=front,
                     joinstyle="round")


def icon_stop(c, cx, cy, col):
    col = col or RED
    round_rect(c, cx - 6, cy - 6, cx + 6, cy + 6, 2, fill=col, outline=col)


def icon_launch(c, cx, cy, col):
    col = col or GREEN
    c.create_polygon([cx - 5, cy - 7, cx - 5, cy + 7, cx + 7, cy], fill=col, outline=col, joinstyle="round")


def icon_save(c, cx, cy, col):
    """A floppy disk: cut-corner outline, shutter at the top, label at the bottom."""
    col = col or BLUE
    x0, y0, x1, y1 = cx - 8, cy - 8, cx + 8, cy + 8
    c.create_line(x0, y0, x1 - 4, y0, x1, y0 + 4, x1, y1, x0, y1, x0, y0, fill=col, width=1.9, joinstyle="round",
                  capstyle="round")
    c.create_line(cx - 4, y0, cx - 4, y0 + 5, cx + 3, y0 + 5, cx + 3, y0, fill=col, width=1.7, joinstyle="round")
    c.create_line(cx - 5, y1, cx - 5, cy + 1, cx + 5, cy + 1, cx + 5, y1, fill=col, width=1.7, joinstyle="round")


def icon_connect(c, cx, cy, col):
    col = col or BLUE
    c.create_rectangle(cx - 4, cy - 8, cx - 2, cy - 2, fill=col, outline=col)
    c.create_rectangle(cx + 2, cy - 8, cx + 4, cy - 2, fill=col, outline=col)
    round_rect(c, cx - 6, cy - 3, cx + 6, cy + 3, 2, fill=col, outline=col)
    c.create_line(cx, cy + 3, cx, cy + 9, fill=col, width=2)


ICONS = {"refresh": icon_refresh, "download": icon_download, "open": icon_open, "stop": icon_stop, "save": icon_save,
         "launch": icon_launch, "connect": icon_connect}


class IconButton(tk.Canvas):
    """A rounded button with a vector icon and a label."""

    LIGHT = dict(face="#ffffff", hover="#f1f1f1", press="#e2e2e2", border="#c8c8c8", text="#1d1d1f",
                 off_face="#f5f5f5", off_border="#dddddd", off_text="#a8a8a8", off_icon="#b5b5b5")
    DARK = dict(face="#5b5b5e", hover="#68686b", press="#48484a", border="#3a3a3c", text="#f5f5f7",
                off_face="#434345", off_border="#3a3a3c", off_text="#7d7d80", off_icon="#7d7d80")

    def __init__(self, master, text, icon, command, state="normal", pad_x=14, height=30, primary=False):
        self._bg = master.winfo_toplevel().cget("background")
        super().__init__(master, highlightthickness=0, bd=0, background=self._bg, cursor="arrow")
        self.text, self.icon, self.command, self.state = text, icon, command, state
        self.primary = primary
        self.hover = self.pressed = False
        self.font = tkfont.nametofont("TkDefaultFont")
        self.pad_x = pad_x
        self.h = height
        self.w = pad_x + 18 + 8 + self.font.measure(text) + pad_x
        self.config(width=self.w, height=self.h)
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<<TkSystemAppearanceChanged>>", lambda e: (theme.refresh(), self.draw()))
        self.draw()

    def set_primary(self, value):
        if bool(value) != self.primary:
            self.primary = bool(value)
            self.draw()

    def _set(self, **kw):
        if self.state == "disabled":
            return
        for k, v in kw.items():
            setattr(self, k, v)
        self.draw()

    def _release(self, e):
        was = self.pressed
        self._set(pressed=False)
        if was and self.state != "disabled" and 0 <= e.x <= self.w and 0 <= e.y <= self.h:
            self.command()

    def config(self, **kw):  # noqa: D102 - keep the ttk-style config(state=...) API
        if "state" in kw:
            self.state = kw.pop("state")
            self.hover = self.pressed = False
            self.draw()
        if kw:
            super().config(**kw)

    configure = config

    def draw(self):
        self.delete("all")
        pal = self.DARK if is_dark(self.winfo_toplevel()) else self.LIGHT
        off = self.state == "disabled"
        face = pal["off_face"] if off else pal["press"] if self.pressed else pal["hover"] if self.hover else pal["face"]
        text_col = pal["off_text"] if off else pal["text"]
        icon_col = pal["off_icon"] if off else None
        border = pal["off_border"] if off else pal["border"]
        if self.primary and not off:   # filled accent button with white content
            face = "#1f6f8f" if self.pressed else "#3a9bc1" if self.hover else BLUE
            text_col, icon_col, border = "#ffffff", "#ffffff", face
        self.config(background=self.winfo_toplevel().cget("background"))
        round_rect(self, 1.5, 1.5, self.w - 1.5, self.h - 1.5, 7, fill=face, outline=border)
        cx, cy = self.pad_x + 9, self.h / 2
        ICONS[self.icon](self, cx, cy, icon_col)
        self.create_text(self.pad_x + 18 + 8, cy, text=self.text, anchor="w", font=self.font, fill=text_col)


class Light(tk.Canvas):
    """A small flat status dot. Green pulses softly while a logger is connected.

    The dot is a vector circle (sharp on Retina); the pulse is a pre-blurred transparent glow image,
    so it blends with whatever is behind it.
    """

    COLOR = {"none": "#a8a8a8", "detected": "#ff9f0a", "ok": "#34c759"}
    PULSE_MS = 110               # frame time; ~20 frames per cycle -> about a 2 second swell

    def __init__(self, master, size=22, bg=None):
        self.size = size
        bg = bg or master.winfo_toplevel().cget("background")
        super().__init__(master, width=size, height=size, highlightthickness=0, bd=0, background=bg)
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        self._halo = {}
        self._frames = []
        try:
            for name in sorted(f[:-4] for f in os.listdir(base) if f.startswith("halo_ok_")):
                self._frames.append(name)
                self._halo[name] = tk.PhotoImage(file=os.path.join(base, name + ".png"))
            self._halo["halo_detected"] = tk.PhotoImage(file=os.path.join(base, "halo_detected.png"))
        except (tk.TclError, OSError):
            self._frames = []   # no glow images: the dot alone still works
        self.state = "none"
        self.frame = 0
        self._job = None
        self.draw()

    def set(self, state):
        self.state = state
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self.frame = 0
        self.draw()
        if state == "ok":
            self._job = self.after(self.PULSE_MS, self._pulse)

    def _pulse(self):
        self.frame = (self.frame + 1) % max(1, len(self._frames))
        self.draw()
        self._job = self.after(self.PULSE_MS, self._pulse)

    def draw(self):
        self.delete("all")
        c = self.size / 2
        halo = {"ok": self._frames[self.frame] if self._frames else None, "detected": "halo_detected"}.get(self.state)
        if halo in self._halo:
            self.create_image(c, c, image=self._halo[halo])
        r = 5.5
        disc(self, c, c, r, self.COLOR[self.state])


class Battery(tk.Canvas):
    """A small battery glyph: outline, terminal nub and a fill that follows the charge estimate."""

    EMPTY_V, FULL_V = 3.0, 3.6   # lithium cell: ~3.6 V fresh, ~3.0 V nearly spent

    def __init__(self, master, bg_key="card_bg", width=44, height=20):
        super().__init__(master, width=width, height=height, highlightthickness=0, bd=0,
                         background=theme.color(bg_key))
        self.w, self.h, self.bg_key = width, height, bg_key
        self.volts = None
        self.draw()

    def set(self, volts):
        self.volts = volts
        self.draw()

    def level(self):
        if self.volts is None:
            return None
        return round(max(0.0, min(1.0, (self.volts - self.EMPTY_V) / (self.FULL_V - self.EMPTY_V))), 4)

    def word(self):
        """Plain-language state. HOBOware shows 'good' at 3.55 V; the other cut-offs are a guess."""
        lvl = self.level()
        return "" if lvl is None else "Good" if lvl >= 0.5 else "Fair" if lvl >= 0.2 else "Low"

    def draw(self):
        self.delete("all")
        bg = theme.color(self.bg_key)
        tk.Canvas.config(self, background=bg)
        w, h = self.w, self.h
        lvl = self.level()
        edge = theme.color("key") if lvl is not None else theme.color("track")
        # whole-pixel geometry, symmetric around the vertical middle: outline 2 px, then a 2 px gap, then the fill
        top, bot = (h - 14) // 2, (h - 14) // 2 + 14            # body 14 px tall
        right = w - 6
        round_rect(self, 1, top, right, bot, 4, fill=bg, outline=edge, width=2)                  # body
        round_rect(self, right + 1, h // 2 - 3, right + 4, h // 2 + 3, 1.5, fill=edge)            # terminal
        if lvl is None:
            return
        color = "#34c759" if lvl >= 0.5 else "#ff9f0a" if lvl >= 0.2 else "#ff453a"
        inner_left, inner_right = 1 + 2 + 2, right - 2 - 2        # outline + gap on both sides
        fill_w = max(3, round((inner_right - inner_left) * lvl))
        round_rect(self, inner_left, top + 4, inner_left + fill_w, bot - 4, 2, fill=color)

class Switch(tk.Canvas):
    """An on/off rocker switch bound to a tk.BooleanVar."""

    W, H = 42, 24

    def __init__(self, master, variable, bg, command=None):
        super().__init__(master, width=self.W, height=self.H, highlightthickness=0, bd=0, background=bg,
                         cursor="arrow")
        self.var, self.command = variable, command
        self.pos = 1.0 if variable.get() else 0.0      # knob position 0 (off) .. 1 (on), animated
        self._job = None
        self.bind("<Button-1>", lambda e: self.toggle())
        self.bind("<<TkSystemAppearanceChanged>>", lambda e: (theme.refresh(), self.draw()))
        self.var.trace_add("write", lambda *a: self._glide())
        self.draw()

    def toggle(self):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _glide(self):
        target = 1.0 if self.var.get() else 0.0
        if self._job:
            self.after_cancel(self._job)

        def step():
            if abs(self.pos - target) < 0.12:
                self.pos, self._job = target, None
            else:
                self.pos += 0.25 if target > self.pos else -0.25
                self._job = self.after(14, step)
            self.draw()
        step()

    def draw(self):
        self.delete("all")
        w, h = self.W, self.H
        on = self.pos
        off_col = (199, 199, 204) if not is_dark(self.winfo_toplevel()) else (99, 99, 102)
        on_col = (52, 199, 89)
        col = _hex(_mix(off_col, on_col, on))
        round_rect(self, 1, 1, w - 1, h - 1, (h - 2) / 2, fill=col, outline=col)
        r = h / 2 - 3
        cx = 3 + r + (w - 6 - 2 * r) * on
        disc(self, cx, h / 2 + 0.9, r + 0.4, "#cfcfd4")   # soft shadow
        disc(self, cx, h / 2, r, "#ffffff")


class GearButton(tk.Canvas):
    """A flat gear icon for the header bar; lights up on hover."""

    def __init__(self, master, command, bg, size=34):
        super().__init__(master, width=size, height=size, highlightthickness=0, bd=0, background=bg, cursor="arrow")
        self.size, self.command, self.bg = size, command, bg
        self.hover = False
        self.bind("<Enter>", lambda e: self._set(True))
        self.bind("<Leave>", lambda e: self._set(False))
        self.bind("<ButtonRelease-1>", lambda e: command() if 0 <= e.x <= size and 0 <= e.y <= size else None)
        self.draw()

    def _set(self, hover):
        self.hover = hover
        self.draw()

    def set_bg(self, color):
        self.bg = color
        self.config(background=color)
        self.draw()

    def draw(self):
        """A standard cog from strokes: a thick ring for the body and eight square teeth."""
        self.delete("all")
        c = self.size / 2
        if self.hover:   # soft round highlight
            disc(self, c, c, self.size / 2 - 2, _hex(_mix(_rgb(self, self.bg), (128, 128, 128), 0.16)))
        col = theme.color("gear_hover" if self.hover else "gear")
        self.create_oval(c - 5.9, c - 5.9, c + 5.9, c + 5.9, outline=col, width=4.4, fill="")   # body, hole inside
        for i in range(8):
            a = 2 * math.pi * i / 8
            self.create_line(c + 7.4 * math.cos(a), c + 7.4 * math.sin(a), c + 11 * math.cos(a), c + 11 * math.sin(a),
                             fill=col, width=3.8, capstyle="butt")


class UnitSwitch(tk.Canvas):
    """A two-segment rocker: °F (warm) | °C (cool). The highlight slides to the chosen side.

    Bound to a tk.BooleanVar that is True for Celsius.
    """

    W, H = 104, 30
    WARM, COOL = (232, 116, 59), (46, 134, 171)

    def __init__(self, master, variable, bg, command=None):
        super().__init__(master, width=self.W, height=self.H, highlightthickness=0, bd=0, background=bg,
                         cursor="arrow")
        self.var, self.command = variable, command
        self.pos = 1.0 if variable.get() else 0.0      # 0 = F, 1 = C (animated)
        self._job = None
        self.bind("<Button-1>", self._click)
        self.bind("<<TkSystemAppearanceChanged>>", lambda e: (theme.refresh(), self.draw()))
        self.var.trace_add("write", lambda *a: self._glide())
        self.draw()

    def _click(self, e):
        want = e.x >= self.W / 2
        if want != self.var.get():
            self.var.set(want)
            if self.command:
                self.command()

    def _glide(self):
        target = 1.0 if self.var.get() else 0.0
        if self._job:
            self.after_cancel(self._job)

        def step():
            if abs(self.pos - target) < 0.12:
                self.pos, self._job = target, None
            else:
                self.pos += 0.22 if target > self.pos else -0.22
                self._job = self.after(14, step)
            self.draw()
        step()

    def draw(self):
        self.delete("all")
        w, h = self.W, self.H
        dark = is_dark()
        track = "#3a3a3c" if dark else "#e3e3e8"
        round_rect(self, 1, 1, w - 1, h - 1, (h - 2) / 2, fill=track, outline=track)
        seg = (w - 6) / 2
        x = 3 + seg * self.pos
        col = _hex(_mix(self.WARM, self.COOL, self.pos))
        round_rect(self, x, 3, x + seg, h - 3, (h - 6) / 2, fill=col, outline=col)
        idle = "#9a9aa0" if not dark else "#8e8e93"
        for label, cx, on in (("°F", 3 + seg / 2, 1 - self.pos), ("°C", 3 + seg * 1.5, self.pos)):
            self.create_text(cx, h / 2, text=label, font=("Helvetica", 13, "bold"),
                             fill=_hex(_mix(_rgb(self, "#9a9aa0") if False else (154, 154, 160), (255, 255, 255), on))
                             if not dark else _hex(_mix((142, 142, 147), (255, 255, 255), on)))


class Tooltip:
    """A small hover hint. `text` may be a string or a function returning one (so it can explain why a button is off)."""

    DELAY_MS = 550

    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _enter(self, _e):
        self._cancel()
        self._job = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show(self):
        self._job = None
        text = self.text() if callable(self.text) else self.text
        if not text or self.tip:
            return
        w = self.widget
        tip = tk.Toplevel(w)
        tip.wm_overrideredirect(True)
        try:
            tip.tk.call("::tk::unsupported::MacWindowStyle", "style", tip._w, "help", "noActivates")
        except tk.TclError:
            pass
        tip.attributes("-topmost", True)
        frame = tk.Frame(tip, background=theme.color("tip_border"), padx=1, pady=1)
        frame.pack()
        tk.Label(frame, text=text, background=theme.color("tip_bg"), foreground=theme.color("tip_text"),
                 font=("Helvetica", 11), justify="left", wraplength=280, padx=8, pady=4).pack()
        tip.update_idletasks()
        x = w.winfo_rootx() + min(24, w.winfo_width() // 2)
        y = w.winfo_rooty() + w.winfo_height() + 6
        sw = w.winfo_screenwidth()
        x = max(4, min(x, sw - tip.winfo_reqwidth() - 8))
        tip.wm_geometry(f"+{x}+{y}")
        self.tip = tip

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class Card(tk.Canvas):
    """A rounded panel. Put content in `.body`. Width follows the space given; height follows the content
    (or fills the space when fill_height=True)."""

    R = 14

    def __init__(self, master, pad=16, fill_height=False):
        super().__init__(master, highlightthickness=0, bd=0, background=theme.color("window_bg"))
        self.pad, self.fill_height = pad, fill_height
        self.body = tk.Frame(self, background=theme.color("card_bg"))
        self._win = self.create_window(pad, pad, window=self.body, anchor="nw")
        self._req = (0, 0)
        self._job = None
        self.bind("<Configure>", self._paint)
        self._job = self.after(50, self._sync)

    def _sync(self):
        """Keep the requested size equal to the content's natural size (so the window can still shrink)."""
        try:
            req = (self.body.winfo_reqwidth() + 2 * self.pad, self.body.winfo_reqheight() + 2 * self.pad)
            if not self.fill_height:   # grow only: a card never shrinks back when a value gets shorter
                req = (max(req[0], self._req[0]), max(req[1], self._req[1]))
            if req != self._req:
                self._req = req
                self.config(width=req[0], height=req[1])
            self._job = self.after(400, self._sync)
        except tk.TclError:
            pass   # window closed

    def destroy(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        super().destroy()

    def _paint(self, e=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("bg")
        round_rect(self, 1, 1, w - 1, h - 1, self.R, fill=theme.color("card_bg"), outline=theme.color("card_border"),
                   tags="bg")
        self.tag_lower("bg")
        self.itemconfigure(self._win, width=max(1, w - 2 * self.pad),
                           height=max(1, h - 2 * self.pad) if self.fill_height else 0)

    def shrink(self):
        """Allow the height to shrink again (cards otherwise only grow, which keeps them from jittering)."""
        self._req = (self._req[0], 0)

    def restyle(self):
        self.config(background=theme.color("window_bg"))
        self.body.config(background=theme.color("card_bg"))
        self._paint()


class Pill(tk.Canvas):
    """A small rounded status badge. kind: ok | idle | off."""

    def __init__(self, master, bg_key="card_bg"):
        super().__init__(master, highlightthickness=0, bd=0, height=26, background=theme.color(bg_key))
        self.bg_key, self.text, self.kind = bg_key, "", "off"
        self.font = tkfont.Font(family=".AppleSystemUIFont", size=12, weight="bold")

    def set(self, text, kind):
        self.text, self.kind = text, kind
        self.draw()

    def draw(self):
        self.delete("all")
        self.config(background=theme.color(self.bg_key))
        w = self.font.measure(self.text) + 38
        self.config(width=w)
        bg, fg = theme.color(f"pill_{self.kind}_bg"), theme.color(f"pill_{self.kind}_fg")
        round_rect(self, 0, 0, w, 26, 13, fill=bg, outline=bg)
        disc(self, 14, 13, 4, fg)
        self.create_text(25, 13, text=self.text, anchor="w", fill=fg, font=self.font)


class ProgressBar(tk.Canvas):
    """A thin rounded progress bar with the few ttk.Progressbar calls the app uses (config, start, stop, [value])."""

    def __init__(self, master, bg_key="window_bg", height=6):
        super().__init__(master, highlightthickness=0, bd=0, height=height, background=theme.color(bg_key))
        self.bg_key, self.h = bg_key, height
        self.mode, self.value, self.maximum = "determinate", 0.0, 100.0
        self._job, self._phase = None, 0.0
        self.bind("<Configure>", lambda e: self.draw())

    def config(self, **kw):  # noqa: D102 - ttk-compatible subset
        for k in ("mode", "value", "maximum"):
            if k in kw:
                setattr(self, k, kw.pop(k))
        if kw:
            super().config(**kw)
        self.draw()

    configure = config

    def cget(self, key):
        return getattr(self, key) if key in ("mode", "value", "maximum") else super().cget(key)

    def __getitem__(self, key):
        return getattr(self, key) if key in ("mode", "value", "maximum") else super().__getitem__(key)

    def __setitem__(self, key, val):
        if key in ("mode", "value", "maximum"):
            setattr(self, key, val)
            self.draw()
        else:
            super().__setitem__(key, val)

    def start(self, ms=12):
        self.stop()
        self._tick()

    def _tick(self):
        self._phase = (self._phase + 0.02) % 1.0
        self.draw()
        self._job = self.after(16, self._tick)

    def stop(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None

    def draw(self):
        self.delete("all")
        self.config_bg()
        w, h = self.winfo_width(), self.h
        if w < 4:
            return
        round_rect(self, 0, 0, w, h, h / 2, fill=theme.color("track"), outline=theme.color("track"))
        if self.mode == "indeterminate":
            seg = w * 0.28
            x = -seg + (w + seg) * self._phase
            x0, x1 = max(0, x), min(w, x + seg)
            if x1 - x0 > h:
                round_rect(self, x0, 0, x1, h, h / 2, fill=theme.color("accent"), outline=theme.color("accent"))
        elif self.value > 0:
            fw = max(h, w * min(1.0, self.value / self.maximum))
            round_rect(self, 0, 0, fw, h, h / 2, fill=theme.color("accent"), outline=theme.color("accent"))

    def config_bg(self):
        tk.Canvas.config(self, background=theme.color(self.bg_key))


class MemoryBar(tk.Canvas):
    """A small rounded bar showing how much of the logger's memory is used: blue, then amber, then red."""

    def __init__(self, master, width=190, height=8, bg_key="card_bg"):
        super().__init__(master, width=width, height=height, highlightthickness=0, bd=0,
                         background=theme.color(bg_key))
        self.w, self.h, self.bg_key = width, height, bg_key
        self.frac = 0.0
        self.draw()

    def set(self, frac):
        self.frac = max(0.0, min(1.0, frac or 0.0))
        self.draw()

    def draw(self):
        self.delete("all")
        tk.Canvas.config(self, background=theme.color(self.bg_key))
        w, h = self.w, self.h
        round_rect(self, 0, 0, w, h, h / 2, fill=theme.color("track"), outline=theme.color("track"))
        if self.frac > 0:
            col = "#ff453a" if self.frac >= 0.95 else "#ff9f0a" if self.frac >= 0.8 else theme.color("accent")
            round_rect(self, 0, 0, max(h, w * self.frac), h, h / 2, fill=col, outline=col)


class CheckItem(tk.Canvas):
    """A numbered step marker. States: blocked (grey ring), pending (accent ring, the current step), done.

    Becoming done plays a short animation: the circle fills green, then a check mark draws itself."""

    CHECK = ((0.30, 0.53), (0.44, 0.67), (0.72, 0.36))     # check mark corners, as fractions of the box

    def __init__(self, master, number, size=28, bg_key="card_bg"):
        super().__init__(master, width=size, height=size, highlightthickness=0, bd=0, background=theme.color(bg_key))
        self.number, self.size, self.bg_key = number, size, bg_key
        self.state, self.progress = "blocked", 0.0
        self._job = None
        self.draw()

    def set_state(self, state, animate=True):
        if state == self.state:
            return
        old, self.state = self.state, state
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        if state == "done" and animate and old != "done":
            self.progress = 0.0
            self._step()
        else:
            self.progress = 1.0 if state == "done" else 0.0
            self.draw()

    def _step(self):
        self.progress = min(1.0, self.progress + 0.07)
        self.draw()
        self._job = self.after(16, self._step) if self.progress < 1.0 else None

    def destroy(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
        super().destroy()

    def draw(self):
        self.delete("all")
        tk.Canvas.config(self, background=theme.color(self.bg_key))
        S = self.size
        c, r = S / 2, S / 2 - 3
        if self.state == "done":
            grow = min(1.0, self.progress / 0.4)               # first 40 %: the green disc grows in
            if grow < 1.0:
                self.create_oval(c - r, c - r, c + r, c + r, outline=theme.color("track"), width=2, fill="")
            if grow > 0:
                disc(self, c, c, r * grow, "#34c759")
            t = max(0.0, (self.progress - 0.4) / 0.6)          # then the check mark draws itself
            if t > 0:
                pts = [(x * S, y * S) for x, y in self.CHECK]
                seg = [math.dist(pts[0], pts[1]), math.dist(pts[1], pts[2])]
                reach, path = t * sum(seg), [pts[0]]
                for i, length in enumerate(seg):
                    if reach >= length:
                        path.append(pts[i + 1])
                        reach -= length
                    else:
                        a, b = pts[i], pts[i + 1]
                        path.append((a[0] + (b[0] - a[0]) * reach / length, a[1] + (b[1] - a[1]) * reach / length))
                        break
                if len(path) >= 2:
                    self.create_line(*[v for pt in path for v in pt], fill="#ffffff", width=2.8, capstyle="round",
                                     joinstyle="round")
            return
        ring = theme.color("accent") if self.state == "pending" else theme.color("track")
        self.create_oval(c - r, c - r, c + r, c + r, outline=ring, width=2, fill="")
        self.create_text(c, c, text=str(self.number), font=(".AppleSystemUIFont", 12, "bold"),
                         fill=theme.color("accent") if self.state == "pending" else theme.color("key"))
