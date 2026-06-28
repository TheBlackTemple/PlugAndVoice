#!/usr/bin/env python3
"""
run_headless.py — MicHost headless CLI harness (Modules 1 + 2).

Usage:
    python run_headless.py                      # load settings, prompt for missing devices
    python run_headless.py --in 1 --out 4       # override device indices directly
    python run_headless.py --list               # print qualified device table and exit
    python run_headless.py --settings           # print current settings and exit

Device selection priority (highest to lowest):
  1. --in / --out CLI flags (index-based, bypass all settings logic)
  2. Saved settings (input_device / output_device by qualified name)
  3. Interactive prompt, pre-filled with suggest_devices() heuristic

Press Ctrl+C to stop.
"""

import argparse
import os
import sys
import time

# Ensure project root is on the path regardless of working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Bootstrap logging via settings before any other import ──────────────────
from settings import setup_logging, ensure_dirs
setup_logging()
ensure_dirs()

import logging
log = logging.getLogger("run_headless")

from settings import (
    load_settings,
    save_settings,
    enumerate_devices,
    suggest_devices,
    validate_devices,
    find_device_by_name,
    asio_available,
    vbcable_present,
    scan_vst3,
    SUPPORTED_BLOCK_SIZES,
)
from engine import AudioEngine


# ── Dependency guard ─────────────────────────────────────────────────────────

def _require_imports() -> None:
    missing = []
    for pkg in ("sounddevice", "pedalboard", "numpy"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"ERROR: Missing packages: {', '.join(missing)}")
        print("Install with:  pip install sounddevice pedalboard numpy")
        sys.exit(1)


# ── Device display ───────────────────────────────────────────────────────────

def print_device_table(devices) -> None:
    """Print the full host-API-qualified device table."""
    print(f"\n  {'IDX':>4}  {'IN':>3}  {'OUT':>3}  {'WASAPI':>6}  {'VBCBL':>5}  QUALIFIED NAME")
    print(f"  {'─'*4}  {'─'*3}  {'─'*3}  {'─'*6}  {'─'*5}  {'─'*48}")
    for d in devices:
        flags = ""
        if d.is_wasapi:
            flags += " W"
        if d.is_vbcable:
            flags += " V"
        print(
            f"  {d.index:>4}  {d.max_inputs:>3}  {d.max_outputs:>3}  "
            f"{'YES' if d.is_wasapi else '':>6}  "
            f"{'YES' if d.is_vbcable else '':>5}  "
            f"{d.qualified}"
        )
    print()


# ── Interactive device picker ─────────────────────────────────────────────────

def pick_device_interactive(
    devices,
    kind: str,          # "input" or "output"
    suggested,          # DeviceEntry | None
    saved_name: str,    # qualified name from settings, or ""
) -> "DeviceEntry":
    """
    Prompt the user to pick a device by index.

    Default priority:
      1. Saved settings name (if it resolves to a live device)
      2. suggest_devices() heuristic
      3. First available device of the right kind
    """
    # Resolve saved name → DeviceEntry
    saved_dev = find_device_by_name(saved_name, devices) if saved_name else None

    default_dev = saved_dev or suggested
    if default_dev is None:
        # Last resort: first device with the right channel type
        candidates = [d for d in devices if (d.is_input if kind == "input" else d.is_output)]
        default_dev = candidates[0] if candidates else None

    default_idx = default_dev.index if default_dev else 0
    default_label = f"{default_idx} ({default_dev.qualified})" if default_dev else str(default_idx)

    print_device_table(devices)
    raw = input(f"  {kind.capitalize()} device index [default={default_label}]: ").strip()
    if raw == "":
        if default_dev is None:
            print("  ERROR: No default device available. Specify an index.")
            sys.exit(1)
        return default_dev

    try:
        idx = int(raw)
    except ValueError:
        print(f"  ERROR: '{raw}' is not a valid index.")
        sys.exit(1)

    found = next((d for d in devices if d.index == idx), None)
    if found is None:
        print(f"  ERROR: No device at index {idx}.")
        sys.exit(1)
    return found


# ── Plugin chain builder ──────────────────────────────────────────────────────

def build_chain(vst3_paths: list[str]) -> list:
    """
    Load each .vst3 path into its own Pedalboard.
    Returns list of [Pedalboard, bypass_flag].
    Failed loads are skipped with a warning (Section 8.3).
    """
    from pedalboard import Pedalboard, load_plugin

    chain = []
    for path in vst3_paths:
        log.info("Loading plugin: %s", path)
        try:
            plugin = load_plugin(path)
            board = Pedalboard([plugin])
            chain.append([board, False])
            log.info("  OK — %s", os.path.basename(path))
        except Exception as e:
            log.warning("  SKIPPED (load failed): %s — %s", path, e)

    if not chain:
        log.info("Chain is empty — audio will pass through unprocessed.")
    return chain


# ── Live meter display ────────────────────────────────────────────────────────

def print_meters(payload: dict) -> None:
    inp = payload["input"]
    master = payload["master"]
    plugins = payload.get("plugins", [])

    parts = [f"IN  rms={inp['rms']:+6.1f} pk={inp['peak']:+6.1f}"]
    for i, m in enumerate(plugins):
        parts.append(f"| P{i} rms={m['rms']:+6.1f} pk={m['peak']:+6.1f}")
    parts.append(f"| OUT rms={master['rms']:+6.1f} pk={master['peak']:+6.1f} dBFS")

    print("\r  " + "  ".join(parts), end="", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _require_imports()

    parser = argparse.ArgumentParser(description="MicHost headless engine harness")
    parser.add_argument("--list",     action="store_true", help="Print device table and exit")
    parser.add_argument("--settings", action="store_true", help="Print current settings and exit")
    parser.add_argument("--in",  dest="in_dev",  type=int, default=None,
                        help="Input device index (overrides settings)")
    parser.add_argument("--out", dest="out_dev", type=int, default=None,
                        help="Output device index (overrides settings)")
    parser.add_argument("--samplerate", type=float, default=None,
                        help="Sample rate override (default: device native)")
    parser.add_argument("--blocksize", type=int, default=None,
                        help=f"Block size in frames (default: from settings)")
    parser.add_argument("--save", action="store_true",
                        help="Save chosen devices back to settings on exit")
    args = parser.parse_args()

    # ── Enumerate once; used everywhere below ────────────────────────────────
    devices = enumerate_devices()
    if not devices:
        print("ERROR: No audio devices found. Is PortAudio installed?")
        sys.exit(1)

    # ── --list ────────────────────────────────────────────────────────────────
    if args.list:
        print_device_table(devices)
        suggestions = suggest_devices(devices)
        print("  Suggested input :", suggestions.input.qualified if suggestions.input else "(none)")
        print("  Suggested output:", suggestions.output.qualified if suggestions.output else "(none)")
        if suggestions.vbcable_missing:
            print("  [WARNING] No VB-Cable device found.")
        print(f"\n  ASIO available: {'YES' if asio_available() else 'NO'}")
        return

    # ── Load settings ─────────────────────────────────────────────────────────
    settings = load_settings()

    # ── --settings ────────────────────────────────────────────────────────────
    if args.settings:
        print("\n  Current settings:")
        for k, v in settings.items():
            print(f"    {k:20s} = {v!r}")

        val = validate_devices(settings, devices)
        print(f"\n  Device validation: {'OK' if val.ok else 'FAILED'}")
        if val.input_missing:
            print(f"    Input  {val.input_name!r} — NOT FOUND in current enumeration")
        if val.output_missing:
            print(f"    Output {val.output_name!r} — NOT FOUND in current enumeration")
        print()
        return

    # ── VB-Cable warning (Section 8.2) ────────────────────────────────────────
    if not vbcable_present(devices):
        print("\n  [WARNING] No VB-Cable device found.")
        print("  Output will route to a real audio device — be aware of feedback risk.")
        confirm = input("  Continue anyway? [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Aborted.")
            sys.exit(0)

    # ── ASIO notice (Section 8.5) ─────────────────────────────────────────────
    if settings.get("asio") and not asio_available():
        log.warning("ASIO requested in settings but no ASIO host API found — ignoring.")

    # ── Resolve devices ───────────────────────────────────────────────────────
    suggestions = suggest_devices(devices)

    if args.in_dev is not None:
        # Index supplied directly — look it up for its qualified name
        in_entry = next((d for d in devices if d.index == args.in_dev), None)
        if in_entry is None:
            print(f"ERROR: No device at index {args.in_dev}.")
            sys.exit(1)
    else:
        in_entry = pick_device_interactive(
            devices, "input",
            suggested=suggestions.input,
            saved_name=settings.get("input_device") or "",
        )

    if args.out_dev is not None:
        out_entry = next((d for d in devices if d.index == args.out_dev), None)
        if out_entry is None:
            print(f"ERROR: No device at index {args.out_dev}.")
            sys.exit(1)
    else:
        out_entry = pick_device_interactive(
            devices, "output",
            suggested=suggestions.output,
            saved_name=settings.get("output_device") or "",
        )

    # ── Sample rate ───────────────────────────────────────────────────────────
    if args.samplerate is not None:
        samplerate = float(args.samplerate)
        log.info("Sample rate overridden by CLI: %g Hz", samplerate)
    elif settings.get("samplerate") is not None:
        samplerate = float(settings["samplerate"])
        log.info("Sample rate from settings: %g Hz", samplerate)
    else:
        samplerate = float(in_entry.default_samplerate)
        log.info("Sample rate from device native: %g Hz", samplerate)

    # ── Block size ────────────────────────────────────────────────────────────
    blocksize = args.blocksize or settings.get("blocksize") or 256
    if blocksize not in SUPPORTED_BLOCK_SIZES:
        log.warning(
            "Block size %d is not in supported list %s — using it anyway.",
            blocksize, SUPPORTED_BLOCK_SIZES,
        )

    # ── VST3 scan + chain build ───────────────────────────────────────────────
    vst3_paths = scan_vst3()
    log.info("Found %d plugin(s)", len(vst3_paths))
    chain = build_chain(vst3_paths)

    # ── Start engine ──────────────────────────────────────────────────────────
    engine = AudioEngine()
    engine.set_chain(chain)

    log.info(
        "Starting engine — in=[%d] %s | out=[%d] %s | %g Hz | block %d",
        in_entry.index, in_entry.qualified,
        out_entry.index, out_entry.qualified,
        samplerate, blocksize,
    )

    try:
        engine.start(
            input_device=in_entry.index,
            output_device=out_entry.index,
            samplerate=samplerate,
            blocksize=blocksize,
        )
    except Exception as e:
        log.error("Engine failed to start: %s", e)
        sys.exit(1)

    # ── Save chosen devices back to settings if requested ─────────────────────
    if args.save:
        settings["input_device"] = in_entry.qualified
        settings["output_device"] = out_entry.qualified
        settings["samplerate"] = samplerate if args.samplerate else None
        settings["blocksize"] = blocksize
        save_settings(settings)
        log.info("Settings saved.")

    # ── Run loop ──────────────────────────────────────────────────────────────
    info = engine.stream_info
    print(f"\n  Engine running — Ctrl+C to stop.\n")
    print(f"  In  : {info['input_device']} ({info['in_channels']}ch)")
    print(f"  Out : {info['output_device']} (2ch)")
    print(f"  Rate: {info['samplerate']:.0f} Hz  |  block {info['blocksize']}  |  {info['latency_ms']} ms nominal")
    print(f"  Chain: {len(chain)} plugin(s)\n")

    meter_poll = 0.033   # ~30 Hz
    last_xruns = 0

    try:
        while True:
            time.sleep(meter_poll)

            xruns = engine.xrun_count
            if xruns != last_xruns:
                print(f"\n  *** XRUN (total: {xruns}) ***")
                last_xruns = xruns

            if engine.meter_q:
                print_meters(engine.meter_q[-1])

    except KeyboardInterrupt:
        print("\n\n  Stopping…")

    finally:
        engine.stop()
        log.info("Final xrun count: %d", engine.xrun_count)
        print("  Done.")


if __name__ == "__main__":
    main()
