"""
devices.py — device enumeration, qualification, and validation logic.

All functions work against the live sounddevice enumeration.  Nothing here
touches the engine; this is pure query/classification logic consumed by both
the settings GUI and the startup validation gate.

Public API:
  enumerate_devices() -> list[DeviceEntry]
      Full device list, host-API-qualified, preserving duplicates (Section 8.1).

  rank_input_candidates(devices, all_apis=False) -> list[DeviceEntry]
      Up to 3 ranked input suggestions, WASAPI-only by default (Section 8.2).

  rank_output_candidates(devices, all_apis=False) -> list[DeviceEntry]
      Up to 3 ranked output suggestions, WASAPI-only by default (Section 8.2).

  suggest_devices(devices) -> SuggestedDevices
      Convenience wrapper: returns top input + output candidate as a pair.
      Used by headless harness and refresh_devices(); GUI uses rank_* directly.

  validate_pair(in_dev, out_dev) -> PairValidation
      Per-change conflict check: loopback, cross-API mismatch, no VB-Cable.

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
from enum import Enum

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
    Convenience pair of top input/output candidates.
    Used by the headless harness and refresh_devices(); the GUI uses
    rank_input_candidates / rank_output_candidates directly.
    Both may be None if no good candidate is found.
    These are SUGGESTIONS presented to the user, not auto-selections.
    """
    input: DeviceEntry | None = None
    output: DeviceEntry | None = None
    vbcable_missing: bool = False


class PairSeverity(Enum):
    OK   = "ok"    # green — safe to apply
    WARN = "warn"  # amber — works but user should know
    BLOCK = "block" # red  — do not allow Apply


@dataclass
class PairValidation:
    """
    Result of validate_pair(in_dev, out_dev).
    Checked live on every combo change; blocks Apply when severity is BLOCK.
    """
    severity: PairSeverity
    message: str

    @property
    def ok(self) -> bool:
        return self.severity == PairSeverity.OK

    @property
    def warn(self) -> bool:
        return self.severity == PairSeverity.WARN

    @property
    def block(self) -> bool:
        return self.severity == PairSeverity.BLOCK


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
# Ranked candidates (Section 8.2)
# ---------------------------------------------------------------------------

def rank_input_candidates(
    devices: list[DeviceEntry],
    all_apis: bool = False,
) -> list[DeviceEntry]:
    """
    Return ranked input device candidates.

    Ranking order:
      1. WASAPI + mic-like name  (best)
      2. WASAPI, any input, not VB-Cable
      3. Any input, not VB-Cable  (only reachable when all_apis=True or no WASAPI found)

    When all_apis=False (default): WASAPI entries only, capped at _MAX_CANDIDATES.
    When all_apis=True: full enumeration, all matching entries, no cap —
      used by the "Show all audio APIs" power toggle so nothing is hidden.

    VB-Cable inputs are excluded regardless of mode; they are loopback
    devices, not mic sources.
    """
    pool = [d for d in devices if d.is_input and not d.is_vbcable]

    if not all_apis:
        wasapi_pool = [d for d in pool if d.is_wasapi]
        # If no WASAPI inputs at all, fall through to full pool so the
        # user isn't left with an empty combo on non-WASAPI systems.
        if wasapi_pool:
            pool = wasapi_pool

    mic_kw = ("mic", "microphone", "input")

    # Partition into tiers then flatten, preserving stable order within each.
    tier1 = [d for d in pool if d.is_wasapi and _name_suggests_mic(d.name, mic_kw)]
    tier2 = [d for d in pool if d.is_wasapi and d not in tier1]
    tier3 = [d for d in pool if not d.is_wasapi]

    ranked = tier1 + tier2 + tier3
    log.debug("rank_input_candidates → %d entries (all_apis=%s)", len(ranked), all_apis)
    return ranked


def rank_output_candidates(
    devices: list[DeviceEntry],
    all_apis: bool = False,
) -> list[DeviceEntry]:
    """
    Return ranked output device candidates.

    Ranking order:
      1. VB-Cable WASAPI output  (best — silent routing to DAW/Discord)
      2. VB-Cable non-WASAPI output
      3. Non-VB-Cable WASAPI output  (flagged warn at pair-validation time)
      4. Any other output  (only reachable when all_apis=True or no WASAPI found)

    When all_apis=False (default): WASAPI entries only, capped at _MAX_CANDIDATES.
    When all_apis=True: full enumeration, all matching entries, no cap —
      used by the "Show all audio APIs" power toggle so nothing is hidden.
    """
    pool = [d for d in devices if d.is_output]

    if not all_apis:
        wasapi_pool = [d for d in pool if d.is_wasapi]
        if wasapi_pool:
            pool = wasapi_pool

    tier1 = [d for d in pool if d.is_vbcable and d.is_wasapi]
    tier2 = [d for d in pool if d.is_vbcable and not d.is_wasapi]
    tier3 = [d for d in pool if not d.is_vbcable and d.is_wasapi]
    tier4 = [d for d in pool if not d.is_vbcable and not d.is_wasapi]

    ranked = tier1 + tier2 + tier3 + tier4
    log.debug("rank_output_candidates → %d entries (all_apis=%s)", len(ranked), all_apis)
    return ranked


# ---------------------------------------------------------------------------
# Pair validation (live, per-change)
# ---------------------------------------------------------------------------

def validate_pair(
    in_dev: DeviceEntry | None,
    out_dev: DeviceEntry | None,
) -> PairValidation:
    """
    Check the selected input+output pair for known broken or degraded states.

    Called on every combo change in the settings GUI; result drives the
    status indicator and gates the Apply button.

    Severity rules (first match wins):
      BLOCK — same device index (certain feedback loop)
      BLOCK — same bare device name (physical loopback even across host APIs)
      BLOCK — both are VB-Cable (VB-Cable → VB-Cable loops back silently)
      BLOCK — cross-API pairing with exclusive mode implied (MME in, WASAPI out
               or vice versa): stream will fail to open at engine start
      WARN  — output is not VB-Cable (audio exits to speakers; other apps
               won't receive the processed signal)
      WARN  — mismatched host APIs when both are non-VB-Cable real devices
               (works but latency alignment is undefined)
      OK    — WASAPI in, VB-Cable WASAPI out, no conflicts
    """
    if in_dev is None or out_dev is None:
        return PairValidation(
            severity=PairSeverity.WARN,
            message="Select both an input and an output device.",
        )

    # ── BLOCK cases ──────────────────────────────────────────────────────────

    if in_dev.index == out_dev.index:
        return PairValidation(
            severity=PairSeverity.BLOCK,
            message="Input and output are the same device — this will cause a feedback loop.",
        )

    if in_dev.name.lower() == out_dev.name.lower():
        return PairValidation(
            severity=PairSeverity.BLOCK,
            message=(
                f'"{in_dev.name}" is the same physical device on different drivers — '
                "pick one API or use separate devices."
            ),
        )

    if in_dev.is_vbcable and out_dev.is_vbcable:
        return PairValidation(
            severity=PairSeverity.BLOCK,
            message="Both devices are VB-Cable — audio will loop back on itself silently.",
        )

    if in_dev.is_wasapi != out_dev.is_wasapi:
        # WASAPI and MME cannot share a stream; engine.start() will fail.
        in_api  = "WASAPI" if in_dev.is_wasapi  else in_dev.host_api
        out_api = "WASAPI" if out_dev.is_wasapi else out_dev.host_api
        return PairValidation(
            severity=PairSeverity.BLOCK,
            message=(
                f"Input ({in_api}) and output ({out_api}) use different audio drivers — "
                "both must use the same API. Switch to WASAPI on both sides."
            ),
        )

    # ── WARN cases ───────────────────────────────────────────────────────────

    if not out_dev.is_vbcable:
        return PairValidation(
            severity=PairSeverity.WARN,
            message=(
                "Output is a real speaker or headphone — processed audio will play "
                "out loud. Other apps won't receive the signal. "
                "Install VB-Cable to route silently."
            ),
        )

    # ── OK ───────────────────────────────────────────────────────────────────

    return PairValidation(
        severity=PairSeverity.OK,
        message="Looks good.",
    )


# ---------------------------------------------------------------------------
# Convenience wrapper (used by headless harness / refresh_devices)
# ---------------------------------------------------------------------------

def suggest_devices(devices: list[DeviceEntry]) -> SuggestedDevices:
    """
    Return the top-ranked input and output candidate as a convenience pair.

    The GUI uses rank_input_candidates / rank_output_candidates directly to
    populate dropdowns; this wrapper exists for the headless harness and for
    refresh_devices() which only needs the single best pick.
    """
    inputs  = rank_input_candidates(devices)
    outputs = rank_output_candidates(devices)

    suggested_in  = inputs[0]  if inputs  else None
    suggested_out = outputs[0] if outputs else None
    vbcable_missing = not vbcable_present(devices)

    return SuggestedDevices(
        input=suggested_in,
        output=suggested_out,
        vbcable_missing=vbcable_missing,
    )


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
    for d in devices:
        if d.qualified.lower() == name_lower:
            return d
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
    in_name  = settings.get("input_device")  or ""
    out_name = settings.get("output_device") or ""

    in_found  = find_device_by_name(in_name,  devices) is not None if in_name  else False
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _name_suggests_mic(name: str, keywords: tuple) -> bool:
    name_lower = name.lower()
    return any(kw in name_lower for kw in keywords)
