"""Generate button icons (hobo/assets/*.png) and the app icon (assets/app.icns).

Dev-time only; needs Pillow. The generated files are committed, so users don't need to run this.
Run from the project folder:  .venv/bin/python scripts/make_icons.py
"""
import math
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "hobo", "assets")
BLUE, RED, GREEN, GRAY = "#2e86ab", "#d1495b", "#3a9d5d", "#555555"
S = 8  # supersampling factor for smooth edges


def canvas(size=20):
    n = size * S
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img), n


def finish(img, size=20):
    return img.resize((size, size), Image.LANCZOS)


def p(n, x, y):  # unit coords (0-1) -> pixels
    return x * n, y * n


def icon_connect():
    img, d, n = canvas()
    w = int(n * 0.085)
    # plug body, two prongs, cord
    d.rounded_rectangle([n * .26, n * .30, n * .74, n * .62], radius=n * .06, fill=BLUE)
    d.rectangle([n * .36, n * .12, n * .43, n * .32], fill=BLUE)
    d.rectangle([n * .57, n * .12, n * .64, n * .32], fill=BLUE)
    d.rectangle([n * .46, n * .60, n * .54, n * .90], fill=BLUE)
    return finish(img)


def icon_download():
    img, d, n = canvas()
    w = int(n * 0.10)
    d.line([p(n, .5, .10), p(n, .5, .62)], fill=BLUE, width=w)
    d.polygon([p(n, .26, .46), p(n, .74, .46), p(n, .5, .74)], fill=BLUE)
    d.line([p(n, .18, .70), p(n, .18, .88), p(n, .82, .88), p(n, .82, .70)], fill=BLUE, width=w, joint="curve")
    return finish(img)


def icon_stop():
    img, d, n = canvas()
    d.rounded_rectangle([n * .20, n * .20, n * .80, n * .80], radius=n * .10, fill=RED)
    return finish(img)


def icon_launch():
    img, d, n = canvas()
    d.polygon([p(n, .26, .16), p(n, .26, .84), p(n, .82, .50)], fill=GREEN)
    return finish(img)


def icon_open():
    img, d, n = canvas()
    w = int(n * 0.08)
    d.line([p(n, .12, .78), p(n, .12, .22), p(n, .40, .22), p(n, .48, .32), p(n, .84, .32), p(n, .84, .78), p(n, .12, .78)],
           fill=GRAY, width=w, joint="curve")
    d.polygon([p(n, .12, .78), p(n, .24, .46), p(n, .94, .46), p(n, .84, .78)], fill=BLUE)
    return finish(img)


def icon_refresh():
    img, d, n = canvas()
    w = int(n * 0.10)
    box = [n * .18, n * .18, n * .82, n * .82]
    d.arc(box, start=40, end=330, fill=BLUE, width=w)
    a = math.radians(40)
    cx = cy = n * .5
    r = n * .32
    tip = (cx + r * math.cos(a), cy - r * math.sin(a))
    d.polygon([(tip[0] + n * .20, tip[1] - n * .02), (tip[0] - n * .02, tip[1] - n * .16), (tip[0] - n * .06, tip[1] + n * .13)],
              fill=BLUE)
    return finish(img)


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
    for name, fn in (("connect", icon_connect), ("download", icon_download), ("stop", icon_stop),
                     ("launch", icon_launch), ("open", icon_open), ("refresh", icon_refresh)):
        fn().save(os.path.join(ASSETS, name + ".png"))
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
