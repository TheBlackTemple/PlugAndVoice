"""
settings — settings persistence, device logic, and logging setup for MicHost.

Public surface:

  From io:
    setup_logging()      — call once at startup before anything else
    load_settings()      — returns fully-populated settings dict
    save_settings(d)     — atomic write to disk
    ensure_dirs()        — create ./vst3 and ./presets if absent

  From devices:
    enumerate_devices()  -> list[DeviceEntry]
    suggest_devices(devs) -> SuggestedDevices
    find_device_by_name(name, devs) -> DeviceEntry | None
    validate_devices(settings, devs) -> ValidationResult
    asio_available()     -> bool
    vbcable_present(devs) -> bool
    scan_vst3(dir)       -> list[str]

  From defaults:
    DEFAULTS, SETTINGS_PATH, LOG_PATH, VST3_DIR, PRESETS_DIR,
    SUPPORTED_SAMPLE_RATES, SUPPORTED_BLOCK_SIZES
"""

from .io import setup_logging, load_settings, save_settings, ensure_dirs
from .devices import (
    DeviceEntry,
    SuggestedDevices,
    ValidationResult,
    PairSeverity,
    enumerate_devices,
    suggest_devices,
    rank_input_candidates,
    rank_output_candidates,
    validate_pair,
    find_device_by_name,
    validate_devices,
    asio_available,
    vbcable_present,
    scan_vst3,
)
from .defaults import (
    DEFAULTS,
    SETTINGS_PATH,
    LOG_PATH,
    VST3_DIR,
    PRESETS_DIR,
    SESSION_PATH,
    SUPPORTED_SAMPLE_RATES,
    SUPPORTED_BLOCK_SIZES,
)

__all__ = [
    # io
    "setup_logging", "load_settings", "save_settings", "ensure_dirs",
    # devices
    "DeviceEntry", "SuggestedDevices", "ValidationResult", "PairSeverity",
    "enumerate_devices", "suggest_devices", "find_device_by_name",
    "rank_input_candidates", "rank_output_candidates", "validate_pair",
    "validate_devices", "asio_available", "vbcable_present", "scan_vst3",
    # defaults
    "DEFAULTS", "SETTINGS_PATH", "LOG_PATH", "VST3_DIR", "PRESETS_DIR",
    "SESSION_PATH", "SUPPORTED_SAMPLE_RATES", "SUPPORTED_BLOCK_SIZES",
]
