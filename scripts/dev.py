"""Live preview: run the window from source and restart it whenever a file in hobo/ changes.

    .venv/bin/python scripts/dev.py

Edit and save a file; the window closes and reopens with the change in about a second. No rebuild needed.
Ctrl-C to quit. (The installed "HOBO Logger.app" is a separate frozen copy and only updates when rebuilt.)
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH = os.path.join(ROOT, "hobo")


def snapshot():
    out = {}
    for base, _dirs, files in os.walk(WATCH):
        for f in files:
            if f.endswith((".py", ".png")):
                p = os.path.join(base, f)
                out[p] = os.stat(p).st_mtime
    return out


def start():
    print("[dev] starting window", flush=True)
    return subprocess.Popen([sys.executable, "-m", "hobo", "gui"], cwd=ROOT)


def main():
    proc, seen = start(), snapshot()
    try:
        while True:
            time.sleep(0.5)
            now = snapshot()
            if now != seen:
                changed = [os.path.relpath(p, ROOT) for p in now if now.get(p) != seen.get(p)]
                print(f"[dev] changed: {', '.join(changed) or 'files removed'}; restarting", flush=True)
                seen = now
                proc.terminate()
                proc.wait()
                proc = start()
            elif proc.poll() is not None:      # window was closed by hand: stay quiet until a file changes
                time.sleep(0.5)
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
