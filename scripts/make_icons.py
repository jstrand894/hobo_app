"""Generate the glow images for the connection light (hobo/assets/halo_*.png) and the app icon.

Button icons and the lamp itself are vector drawings in hobo/widgets.py, so they stay sharp on Retina.

Dev-time only; needs Pillow. The generated files are committed, so users don't need to run this.
Run from the project folder:  .venv/bin/python scripts/make_icons.py
"""
import math
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "hobo", "assets")
BLUE, RED, GREEN, GRAY = "#2e86ab", "#d1495b", "#3a9d5d", "#555555"
S = 8  # supersampling factor for smooth edges


def halo(rgb, strength, size=44):
    """Soft coloured glow with a transparent background (blurred, so low resolution is invisible)."""
    n = size * 2
    img = Image.new("RGBA", (n, n), rgb + (0,))
    d = ImageDraw.Draw(img)
    r = n * 0.20
    c = n / 2
    d.ellipse([c - r, c - r, c + r, c + r], fill=rgb + (int(255 * strength),))
    img = img.filter(ImageFilter.GaussianBlur(n * 0.06))
    core = Image.new("RGBA", (n, n), rgb + (0,))
    ImageDraw.Draw(core).ellipse([c - r * .75, c - r * .75, c + r * .75, c + r * .75], fill=rgb + (int(150 * strength),))
    img = Image.alpha_composite(img, core.filter(ImageFilter.GaussianBlur(n * 0.04)))
    return img.resize((size, size), Image.LANCZOS)


def app_icon(size=1024):
    n = size
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    m = int(n * 0.06)
    # vertical gradient background in a rounded square
    grad = Image.new("RGBA", (n, n))
    gd = ImageDraw.Draw(grad)
    top, bot = (46, 134, 171), (20, 54, 92)
    for y in range(n):
        t = y / n
        gd.line([(0, y), (n, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, n - m, n - m], radius=int(n * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # white data line with a slow wave, plus axis
    w = int(n * 0.045)
    pts = []
    for i in range(0, 101):
        x = n * (0.20 + 0.60 * i / 100)
        y = n * (0.55 - 0.16 * math.sin(i / 100 * 2 * math.pi * 1.4) - 0.06 * math.sin(i / 100 * 2 * math.pi * 4.3))
        pts.append((x, y))
    d.line([(n * .20, n * .72), (n * .80, n * .72)], fill=(255, 255, 255, 150), width=int(n * .015))
    d.line([(n * .20, n * .30), (n * .20, n * .72)], fill=(255, 255, 255, 150), width=int(n * .015))
    d.line(pts, fill="white", width=w, joint="curve")
    for end in (pts[0], pts[-1]):
        d.ellipse([end[0] - w / 2, end[1] - w / 2, end[0] + w / 2, end[1] + w / 2], fill="white")
    # green "connected" dot
    r = n * 0.075
    cx, cy = n * 0.76, n * 0.27
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#4cd964", outline="white", width=int(n * .012))
    return img


def main():
    os.makedirs(ASSETS, exist_ok=True)
    green, amber = (40, 215, 80), (255, 159, 10)
    frames = 20                                              # frames for a smooth, gentle pulse
    for i in range(frames):
        st = 0.62 + 0.14 * math.sin(2 * math.pi * i / frames)   # 0.48 .. 0.76: a slow, subtle swell
        halo(green, st).save(os.path.join(ASSETS, f"halo_ok_{i:02d}.png"))
    halo(amber, 0.5).save(os.path.join(ASSETS, "halo_detected.png"))
    big = app_icon(1024)
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(ASSETS, "app.png"))
    tmp = tempfile.mkdtemp(suffix=".iconset")
    for sz in (16, 32, 128, 256, 512):
        big.resize((sz, sz), Image.LANCZOS).save(os.path.join(tmp, f"icon_{sz}x{sz}.png"))
        big.resize((sz * 2, sz * 2), Image.LANCZOS).save(os.path.join(tmp, f"icon_{sz}x{sz}@2x.png"))
    out = os.path.join(ROOT, "assets")
    os.makedirs(out, exist_ok=True)
    subprocess.run(["iconutil", "-c", "icns", tmp, "-o", os.path.join(out, "app.icns")], check=True)
    shutil.rmtree(tmp)
    print("wrote icons to", ASSETS, "and", out)


if __name__ == "__main__":
    main()
