#!/usr/bin/env python3
"""
main.py — MicHost application entry point.

Usage:
    python main.py              # launch the GUI
    python run_headless.py      # headless CLI harness (engine + settings, no GUI)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from settings import setup_logging, ensure_dirs
setup_logging()
ensure_dirs()

import logging
log = logging.getLogger("main")

try:
    from gui import launch
except ImportError as e:
    log.error("Could not import GUI: %s", e)
    print(
        f"\nERROR: {e}\n\n"
        "Install GUI dependencies with:\n"
        "  pip install PySide6\n\n"
        "For headless operation (no GUI), use run_headless.py instead."
    )
    sys.exit(1)


if __name__ == "__main__":
    log.info("MicHost starting.")
    launch()
