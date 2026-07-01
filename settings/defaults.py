"""
defaults.py — settings schema version and default values.

The settings file is human-readable JSON, generated on first launch
if missing. Deleting it is safe — it regenerates with these defaults.
"""

SETTINGS_VERSION = 1

# Paths (relative to working directory)
SETTINGS_PATH = "./user_data/host_settings.json"
LOG_PATH = "./user_data/host.log"
VST3_DIR = "./vst3"
PRESETS_DIR = "./presets"
SESSION_PATH = "./user_data/session.json"

# Default settings dict — shape must match Section 6.4 schema.
# input_device: None means "not yet configured" (first-run gate).
# output_device: VB-Cable name as the canonical suggestion; stored as
#   a plain name string. Device lookup at start time uses name-match
#   against the live enumeration (Section 8.6).
DEFAULTS: dict = {
    "version": SETTINGS_VERSION,
    "samplerate": None,      # None = use device native rate (Section 5.1)
    "blocksize": 256,
    "input_device": None,    # None = not yet configured
    "output_device": None,   # None = not yet configured
    "asio": False,
    "autostart": False,
    "last_preset": "",       # Name of last active preset; restored on startup.
    "max_autosaves": 0,      # Per-preset autosave cap; 0 = unlimited.
}

# Sample rates shown in the settings UI as override options
SUPPORTED_SAMPLE_RATES = [44100, 48000, 88200, 96000]

# Block sizes exposed in settings (Section 5.2; 128 proven, 256 default)
SUPPORTED_BLOCK_SIZES = [64, 128, 256, 512, 1024]

# VB-Cable name fragments used for detection (Section 8.2)
VBCABLE_NAME_FRAGMENTS = ("CABLE", "VB-Audio")

# Preferred host API name fragment for VB-Cable and mic selection
PREFERRED_HOST_API = "WASAPI"
