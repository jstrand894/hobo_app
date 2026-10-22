#!/bin/bash
# Build "HOBO Logger.app" into dist/. Needs the project venv (pyinstaller) and Homebrew libusb.
set -e
cd "$(dirname "$0")/.."
LIBUSB=$(python3 -c 'import os;print(os.path.realpath("/opt/homebrew/lib/libusb-1.0.0.dylib"))')
rm -rf build dist
.venv/bin/pyinstaller --noconfirm --windowed \
    --name "HOBO Logger" \
    --icon assets/app.icns \
    --osx-bundle-identifier com.jstrand.hobologger \
    --add-data "hobo/assets:hobo/assets" \
    --add-binary "$LIBUSB:." \
    run_app.py
echo "Built: $PWD/dist/HOBO Logger.app"
