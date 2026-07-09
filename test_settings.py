#!/usr/bin/env python3
"""
test_settings.py — Module 2 smoke test.

Exercises settings I/O and device logic without starting the engine.
Run from the project root:

    python test_settings.py

Expected output:
  - Settings file created (if absent) or loaded cleanly
  - Full device list with host-API qualification
  - Suggested input / output
  - VB-Cable presence flag
  - ASIO availability flag
  - Startup device validation result for the current settings
  - VST3 scan result
"""

import sys

# Bootstrap logging first so all subsequent imports log correctly.
from settings import setup_logging
setup_logging()

import logging
log = logging.getLogger("test_settings")

from settings import (
    ensure_dirs,
    load_settings,
    save_settings,
    enumerate_devices,
    suggest_devices,
    validate_devices,
    find_device_by_name,
    asio_available,
    vbcable_present,
    scan_vst3,
    SETTINGS_PATH,
    LOG_PATH,
    VST3_DIR,
)


def hr(title=""):
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'─' * pad} {title} {'─' * pad}")
    else:
        print("─" * width)


def main():
    print(f"\nPlugAndVoice — Module 2 smoke test")
    print(f"Settings path : {SETTINGS_PATH}")
    print(f"Log path      : {LOG_PATH}")

    # 1. Directory bootstrap
    hr("Directory bootstrap")
    ensure_dirs()
    print(f"  ./vst3    : OK")
    print(f"  ./presets : OK")

    # 2. Settings load
    hr("Settings load/save")
    settings = load_settings()
    print("  Loaded settings:")
    for k, v in settings.items():
        print(f"    {k:20s} = {v!r}")

    # Round-trip save
    save_settings(settings)
    settings2 = load_settings()
    assert settings == settings2, "Round-trip save/load mismatch!"
    print("  Round-trip save/load: OK")

    # 3. Device enumeration
    hr("Device enumeration")
    devices = enumerate_devices()
    if not devices:
        print("  No devices found — is sounddevice installed and PortAudio available?")
        sys.exit(1)

    print(f"  {'IDX':>4}  {'IN':>3}  {'OUT':>3}  {'WASAPI':>6}  {'VBCABLE':>7}  QUALIFIED NAME")
    print(f"  {'─'*4}  {'─'*3}  {'─'*3}  {'─'*6}  {'─'*7}  {'─'*40}")
    for d in devices:
        print(
            f"  {d.index:>4}  {d.max_inputs:>3}  {d.max_outputs:>3}  "
            f"{'YES' if d.is_wasapi else '':>6}  "
            f"{'YES' if d.is_vbcable else '':>7}  "
            f"{d.qualified}"
        )

    # 4. VB-Cable
    hr("VB-Cable detection")
    if vbcable_present(devices):
        vb_devs = [d for d in devices if d.is_vbcable]
        print(f"  VB-Cable FOUND ({len(vb_devs)} entr{'y' if len(vb_devs)==1 else 'ies'}):")
        for d in vb_devs:
            print(f"    [{d.index}] {d.qualified}")
    else:
        print("  VB-Cable NOT found — engine start will require user confirmation.")

    # 5. ASIO
    hr("ASIO availability")
    asio = asio_available()
    print(f"  ASIO available: {'YES' if asio else 'NO (ASIO toggle will be greyed out)'}")

    # 6. Device suggestions
    hr("Device suggestions")
    suggestions = suggest_devices(devices)
    if suggestions.input:
        print(f"  Suggested input : [{suggestions.input.index}] {suggestions.input.qualified}")
        print(f"    (native rate: {suggestions.input.default_samplerate:.0f} Hz)")
    else:
        print("  Suggested input : (none found)")
    if suggestions.output:
        print(f"  Suggested output: [{suggestions.output.index}] {suggestions.output.qualified}")
    else:
        print("  Suggested output: (none found)")
    if suggestions.vbcable_missing:
        print("  [WARNING] No VB-Cable output found — using fallback suggestion.")

    # 7. Startup device validation
    hr("Device validation")
    result = validate_devices(settings, devices)
    print(f"  Configured input  : {settings.get('input_device')!r}")
    print(f"  Configured output : {settings.get('output_device')!r}")
    print(f"  Input present     : {'YES' if not result.input_missing else 'NO — would open Settings view'}")
    print(f"  Output present    : {'YES' if not result.output_missing else 'NO — would open Settings view'}")
    print(f"  Validation OK     : {'YES' if result.ok else 'NO'}")

    if not result.ok:
        print("\n  (Both devices are None in default settings — this is expected on first run.)")
        print("  Simulating a configured-but-missing device:")
        fake_settings = dict(settings)
        fake_settings["input_device"] = "Nonexistent Mic (Windows WASAPI)"
        fake_settings["output_device"] = "CABLE Input (VB-Audio Virtual Cable) (Windows WASAPI)"
        r2 = validate_devices(fake_settings, devices)
        print(f"  Input 'Nonexistent Mic' present  : {'YES' if not r2.input_missing else 'NO (correct)'}")
        print(f"  Output 'CABLE Input...' present  : {'YES (correct)' if not r2.output_missing else 'NO'}")

    # 8. find_device_by_name round-trip
    hr("Name lookup")
    if devices:
        first = devices[0]
        found_q = find_device_by_name(first.qualified, devices)
        found_n = find_device_by_name(first.name, devices)
        print(f"  Lookup by qualified : {'OK' if found_q and found_q.index == first.index else 'FAIL'}")
        print(f"  Lookup by bare name : {'OK' if found_n and found_n.index == first.index else 'FAIL'}")
        print(f"  Lookup nonexistent  : {'OK (None)' if find_device_by_name('__nonexistent__', devices) is None else 'FAIL'}")

    # 9. VST3 scan
    hr("VST3 scan")
    vst3_paths = scan_vst3(VST3_DIR)
    if vst3_paths:
        print(f"  Found {len(vst3_paths)} plugin(s):")
        for p in vst3_paths:
            print(f"    {p}")
    else:
        print(f"  No plugins found in {VST3_DIR}  (drop .vst3 files there to test loading)")

    hr()
    print("\nModule 2 smoke test complete.\n")


if __name__ == "__main__":
    main()
