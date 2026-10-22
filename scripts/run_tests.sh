#!/bin/bash
# Run the whole test suite. Usage:
#   ./scripts/run_tests.sh                 # everything that can run here (fake logger tests + read-only live tests)
#   HOBO_LIVE_TESTS=1 ./scripts/run_tests.sh   # also stop/read out/launch the connected logger, if its name starts with "Test"
# Close HOBOware first if you want the live tests to run (only one program can use the USB device).
cd "$(dirname "$0")/.."
exec .venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v "$@"
