"""
devices.py — device enumeration, qualification, and validation logic.

All functions work against the live sounddevice enumeration.  Nothing here
touches the engine; this is pure query/classification logic consumed by both
the settings GUI and the startup validation gate.

Public API:
  enumerate_devices() -> list[dict]
      Full device list, host-API-qualified, preserving duplicates (Section 8.1).

  suggest_devices(devices) -> SuggestedDevices
      Heuristic best-guess for input and output (Section 8.2).
      Returns names only — the GUI presents them as suggestions, not auto-picks.

  find_device_by_name(name, devices) -> dict | None
      Exact-name lookup against an enumerated list.

  validate_devices(settings, devices) -> ValidationResult
      Startup gate: checks both configured devices are present (Section 8.6).

  asio_available() -> bool
      Returns True if a PortAudio ASIO host API is present (Section 8.5).

  vbcable_present(devices) -> bool
      Returns True if any VB-Cable device appears in the enumeration.

  scan_vst3(vst3_dir) -> list[str]
      Returns sorted list of .vst3 paths found in vst3_dir (Section 8.3).
"""

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Avoid a hard import of sounddevice at module level so that settings
# can be imported in tests / non-audio environments without PortAudio.
try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False
    log.warning("sounddevice not available — device queries will return empty results.")


from .defaults import (
    PREFERRED_HOST_API,
    VBCABLE_NAME_FRAGMENTS,
    VST3_DIR,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DeviceEntry:
    """
    A single host-API-qualified device entry.

    index      — sounddevice device index (passed to sd.Stream)
    name       — raw device name from sounddevice
    host_api   — host API name (e.g. "Windows WASAPI", "MME")
    qualified  — display label: "Name (Host API)"  used in GUI dropdowns
    max_inputs — max input channel count
    max_outputs— max output channel count
    default_samplerate — device's preferred sample rate
    is_input   — has at least one input channel
    is_output  — has at least one output channel
    is_wasapi  — host API contains "WASAPI"
    is_vbcable — name matches a VB-Cable fragment
    """
    index: int
    name: str
    host_api: str
    qualified: str
    max_inputs: int
    max_outputs: int
    default_samplerate: float
    is_input: bool
    is_output: bool
    is_wasapi: bool
    is_vbcable: bool


@dataclass
class SuggestedDevices:
    """
    Heuristic suggestions for input and output device.
    Both may be None if no good candidate is found.
    These are SUGGESTIONS presented to the user, not auto-selections.
    """
    input: DeviceEntry | None = None
    output: DeviceEntry | None = None
    vbcable_missing: bool = False


@dataclass
class ValidationResult:
    """
    Result of the startup device validation gate (Section 8.6).
    ok is True only when both configured devices are present.
    """
    ok: bool
    input_missing: bool = False
    output_missing: bool = False
    input_name: str = ""
    output_name: str = ""


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

def enumerate_devices() -> list[DeviceEntry]:
    """
    Return the full list of devices, one entry per host-API instance.

    sounddevice exposes the same physical device once per host API.
    This is intentional and meaningful (Section 8.1) — do not deduplicate.
    """
    if not _SD_AVAILABLE:
        return []

    try:
        raw_devices = sd.query_devices()
        host_apis = sd.query_hostapis()
    except Exception as e:
        log.error("Failed to enumerate audio devices: %s", e)
        return []

    entries = []
    for idx, dev in enumerate(raw_devices):
        ha_idx = dev.get("hostapi", 0)
        ha_info = host_apis[ha_idx] if ha_idx < len(host_apis) else {}
        ha_name = ha_info.get("name", f"HostAPI-{ha_idx}")

        name = dev.get("name", f"Device-{idx}")
        qualified = f"{name} ({ha_name})"
        is_wasapi = PREFERRED_HOST_API.upper() in ha_name.upper()
        is_vbcable = _is_vbcable_name(name)

        entries.append(DeviceEntry(
            index=idx,
            name=name,
            host_api=ha_name,
            qualified=qualified,
            max_inputs=int(dev.get("max_input_channels", 0)),
            max_outputs=int(dev.get("max_output_channels", 0)),
            default_samplerate=float(dev.get("default_samplerate", 48000.0)),
            is_input=int(dev.get("max_input_channels", 0)) > 0,
            is_output=int(dev.get("max_output_channels", 0)) > 0,
            is_wasapi=is_wasapi,
            is_vbcable=is_vbcable,
        ))

    log.debug("Enumerated %d device entries.", len(entries))
    return entries


# ---------------------------------------------------------------------------
# VB-Cable detection
# ---------------------------------------------------------------------------

def vbcable_present(devices: list[DeviceEntry]) -> bool:
    """Return True if any enumerated device looks like VB-Cable."""
    return any(d.is_vbcable for d in devices)


def _is_vbcable_name(name: str) -> bool:
    name_upper = name.upper()
    return any(frag.upper() in name_upper for frag in VBCABLE_NAME_FRAGMENTS)


# ---------------------------------------------------------------------------
# Device suggestion (Section 8.2)
# ---------------------------------------------------------------------------

def suggest_devices(devices: list[DeviceEntry]) -> SuggestedDevices:
    """
    Return heuristic device suggestions:

    Input suggestion:
      - WASAPI shared, has input channels, not VB-Cable.
      - Prefer devices whose name suggests a microphone
        (contains "mic", "microphone", "input").
      - Fall back to any WASAPI input, then any input.

    Output suggestion:
      - VB-Cable output, prefer WASAPI entry when name appears multiple times.
      - Fall back to any output if no VB-Cable found.

    These are SUGGESTIONS only. The caller (GUI or headless harness) presents
    them to the user; it does not auto-apply them.
    """
    inputs = [d for d in devices if d.is_input and not d.is_vbcable]
    outputs = [d for d in devices if d.is_output]

    suggested_in = _pick_input(inputs)
    suggested_out, vbcable_missing = _pick_output(outputs)

    return SuggestedDevices(
        input=suggested_in,
        output=suggested_out,
        vbcable_missing=vbcable_missing,
    )


def _pick_input(inputs: list[DeviceEntry]) -> DeviceEntry | None:
    if not inputs:
        return None

    mic_keywords = ("mic", "microphone", "input")

    # 1. WASAPI + mic-like name
    wasapi_mic = [
        d for d in inputs
        if d.is_wasapi and _name_suggests_mic(d.name, mic_keywords)
    ]
    if wasapi_mic:
        return wasapi_mic[0]

    # 2. Any WASAPI input
    wasapi_any = [d for d in inputs if d.is_wasapi]
    if wasapi_any:
        return wasapi_any[0]

    # 3. Any input
    return inputs[0]


def _pick_output(outputs: list[DeviceEntry]) -> tuple[DeviceEntry | None, bool]:
    """Returns (suggested_output, vbcable_missing)."""
    vbcable_outputs = [d for d in outputs if d.is_vbcable]

    if not vbcable_outputs:
        # No VB-Cable found — suggest first available output, flag warning
        fallback = outputs[0] if outputs else None
        return fallback, True

    # Prefer WASAPI VB-Cable entry
    wasapi_vb = [d for d in vbcable_outputs if d.is_wasapi]
    if wasapi_vb:
        return wasapi_vb[0], False

    return vbcable_outputs[0], False


def _name_suggests_mic(name: str, keywords: tuple) -> bool:
    name_lower = name.lower()
    return any(kw in name_lower for kw in keywords)


# ---------------------------------------------------------------------------
# Name-based device lookup
# ---------------------------------------------------------------------------

def find_device_by_name(name: str, devices: list[DeviceEntry]) -> DeviceEntry | None:
    """
    Exact name match (case-insensitive) against the qualified display name.
    Used for resolving stored settings names back to live device entries.

    Tries qualified name first, then bare device name, so that settings
    saved as "CABLE Input (VB-Audio Virtual Cable) (Windows WASAPI)" still
    resolves if the host API suffix changes slightly across driver updates.
    """
    if not name:
        return None
    name_lower = name.lower()
    # Exact qualified match
    for d in devices:
        if d.qualified.lower() == name_lower:
            return d
    # Bare name match (fallback)
    for d in devices:
        if d.name.lower() == name_lower:
            return d
    return None


# ---------------------------------------------------------------------------
# Startup device validation (Section 8.6)
# ---------------------------------------------------------------------------

def validate_devices(settings: dict, devices: list[DeviceEntry]) -> ValidationResult:
    """
    Check that both configured devices are present in the live enumeration.

    Returns a ValidationResult with ok=True only if both are found.
    A None configured device is treated as missing (unconfigured = not valid).
    """
    in_name = settings.get("input_device") or ""
    out_name = settings.get("output_device") or ""

    in_found = find_device_by_name(in_name, devices) is not None if in_name else False
    out_found = find_device_by_name(out_name, devices) is not None if out_name else False

    result = ValidationResult(
        ok=in_found and out_found,
        input_missing=not in_found,
        output_missing=not out_found,
        input_name=in_name,
        output_name=out_name,
    )

    if not result.ok:
        missing = []
        if result.input_missing:
            missing.append(f"input={in_name!r}")
        if result.output_missing:
            missing.append(f"output={out_name!r}")
        log.warning("Device validation failed — missing: %s", ", ".join(missing))
    else:
        log.debug("Device validation passed.")

    return result


# ---------------------------------------------------------------------------
# ASIO availability (Section 8.5)
# ---------------------------------------------------------------------------

def asio_available() -> bool:
    """
    Return True if a PortAudio ASIO host API is available.

    The standard sounddevice/PortAudio Windows binary typically does NOT
    include ASIO. Power users can supply an ASIO-enabled PortAudio build.
    This check gates the ASIO toggle in settings.
    """
    if not _SD_AVAILABLE:
        return False
    try:
        apis = sd.query_hostapis()
        return any("asio" in a.get("name", "").lower() for a in apis)
    except Exception as e:
        log.warning("Could not query host APIs for ASIO check: %s", e)
        return False


# ---------------------------------------------------------------------------
# VST3 scanner (Section 8.3)
# ---------------------------------------------------------------------------

def scan_vst3(vst3_dir: str = VST3_DIR) -> list[str]:
    """
    Return a sorted list of .vst3 paths found in vst3_dir.

    Both single-file plugins (Foo.vst3) and bundle directories (Foo.vst3/)
    are returned — Pedalboard handles both transparently.

    Entries that fail os.path checks are silently skipped; the loader
    wraps each load_plugin call in try/except anyway (Section 8.3).
    """
    if not os.path.isdir(vst3_dir):
        log.debug("VST3 directory not found: %s", vst3_dir)
        return []

    found = []
    try:
        for entry in os.listdir(vst3_dir):
            if entry.lower().endswith(".vst3"):
                full = os.path.join(vst3_dir, entry)
                found.append(full)
    except OSError as e:
        log.warning("Error scanning VST3 directory %s: %s", vst3_dir, e)

    found.sort()
    log.debug("VST3 scan found %d plugin(s) in %s", len(found), vst3_dir)
    return found
