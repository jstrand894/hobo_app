"""Command line: python -m hobo status | download [-o DIR] | gui"""
import argparse
import sys

from .actions import describe, launch_logger, read_until_end, save_readout, stop_logging
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
    ro, hobo_path, csv_path = save_readout(mem, args.output, fahrenheit=args.fahrenheit)
    print(f"{ro.name} (serial {ro.serial}): {len(ro.samples)} samples")
    print("wrote", hobo_path)
    print("wrote", csv_path)


def cmd_stop(args):
    with Logger() as lg:
        d = describe(lg)
        if not d["logging"]:
            print(f"{d['name']} (serial {d['serial']}) is not logging.")
            return
        if not args.yes and input(f"Stop logging on {d['name']} (serial {d['serial']})? Data is kept. [y/N] ").lower() != "y":
            return
        d = stop_logging(lg)
    print("stopped" if not d["logging"] else "still logging")


def cmd_launch(args):
    with Logger() as lg:
        d = describe(lg)
        if d["logging"]:
            print(f"{d['name']} is still logging. Download its data and run `hobo stop` first.")
            return
        name = args.name or d["name"]
        print(f"Launch {d['serial']}: name '{name}', interval {args.interval} s, start now.")
        print("This starts a new deployment: the data currently on the logger will be overwritten.")
        if not args.yes and input("Have you saved its data, and do you want to continue? [y/N] ").lower() != "y":
            return
        d = launch_logger(lg, args.interval, name, progress=lambda t: print(t, file=sys.stderr))
    print("logging" if d["logging"] else "not logging", f"- interval {d['interval_s']} s, name {d['name']}")


def cmd_gui(_):
    from .gui import run
    run()


def main():
    p = argparse.ArgumentParser(prog="hobo")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show logger state and identity").set_defaults(fn=cmd_status)
    d = sub.add_parser("download", help="read data off the logger and write .hobo + .csv (does not stop it)")
    d.add_argument("-o", "--output", default="downloads", help="output folder (default: downloads)")
    d.add_argument("-f", "--fahrenheit", action="store_true", help="write temperature in F (default is C)")
    d.set_defaults(fn=cmd_download)
    st = sub.add_parser("stop", help="stop logging (the recorded data is kept)")
    st.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    st.set_defaults(fn=cmd_stop)
    la = sub.add_parser("launch", help="start a new deployment on a stopped logger (overwrites its data)")
    la.add_argument("-i", "--interval", type=int, required=True, help="logging interval in seconds")
    la.add_argument("-n", "--name", help="logger name (default: keep the current name)")
    la.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    la.set_defaults(fn=cmd_launch)
    sub.add_parser("gui", help="open the window").set_defaults(fn=cmd_gui)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
