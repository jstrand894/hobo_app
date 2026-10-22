"""Identify the HOBO optic USB adapter as macOS sees it."""
import glob
import json
import subprocess


def _walk(items):
    for item in items:
        yield item
        yield from _walk(item.get("_items", []))


def usb_devices():
    out = subprocess.run(
        ["system_profiler", "SPUSBDataType", "-json"],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out).get("SPUSBDataType", [])
    return [d for d in _walk(data) if "vendor_id" in d]


def serial_ports():
    return sorted(glob.glob("/dev/cu.*") + glob.glob("/dev/tty.usb*"))


def main():
    devices = usb_devices()
    if not devices:
        print("No USB devices found. Is the adapter plugged in?")
    for d in devices:
        print(f"{d.get('_name')}: vendor={d.get('vendor_id')} "
              f"product={d.get('product_id')} serial={d.get('serial_num')} "
              f"mfr={d.get('manufacturer')}")
    print("Serial ports:", serial_ports() or "none")


if __name__ == "__main__":
    main()
