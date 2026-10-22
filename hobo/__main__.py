"""Command line: python -m hobo status | download [-o DIR] | gui"""
import argparse
import sys

from .actions import describe, read_until_end, save_readout
from .device import Logger


def cmd_status(_):
    with Logger() as lg:
        d = describe(lg)
    print(f"{d['name']} (serial {d['serial']}), interval {d['interval_s']}s, started {d['started']}")
    print("state:", "logging" if d["logging"] else f"idle/stopped (0x{d['state']:02x})")


def cmd_download(args):
    with Logger() as lg:
        mem = read_until_end(lg, progress=lambda a, t: print(f"\rread {a} of {t or '?'} bytes", end="", file=sys.stderr))
        print(file=sys.stderr)
    ro, hobo_path, csv_path = save_readout(mem, args.output, fahrenheit=not args.celsius)
    print(f"{ro.name} (serial {ro.serial}): {len(ro.samples)} samples")
    print("wrote", hobo_path)
    print("wrote", csv_path)


def cmd_gui(_):
    from .gui import run
    run()


def main():
    p = argparse.ArgumentParser(prog="hobo")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show logger state and identity").set_defaults(fn=cmd_status)
    d = sub.add_parser("download", help="read data off the logger and write .hobo + .csv (does not stop it)")
    d.add_argument("-o", "--output", default="downloads", help="output folder (default: downloads)")
    d.add_argument("-c", "--celsius", action="store_true", help="write temperature in C instead of F")
    d.set_defaults(fn=cmd_download)
    sub.add_parser("gui", help="open the window").set_defaults(fn=cmd_gui)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
