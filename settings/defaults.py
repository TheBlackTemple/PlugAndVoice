"""
settings/defaults.py — settings schema version and default values.

The settings file is human-readable JSON, generated on first launch
if missing. Deleting it is safe — it regenerates with these defaults.

PATH NOTE
---------
Path constants here are bare relative strings (no leading "./").
They are resolved to absolute paths at runtime via utils.paths.app_path()
in settings/__init__.py, which anchors them correctly whether running
from source or from a PyInstaller bundle. Do NOT hardcode absolute paths
here — they break when the folder is moved.
"""

SETTINGS_VERSION = 5
from utils.paths import app_path, asset_path

USERDATA_PATH = app_path("user_data")
SETTINGS_PATH = app_path("user_data", "host_settings.json")
LOG_PATH      = app_path("user_data", "host.log")
SESSION_PATH  = app_path("user_data", "session.json")
VST3_DIR      = app_path("vst3")
PRESETS_DIR   = app_path("presets")
AUTOSAVES_DIR = app_path("autosaves")

# Default settings dict — shape must match Section 6.4 schema.
DEFAULTS: dict = {
    "version": SETTINGS_VERSION,
    "samplerate": None,         # None = use device native rate (Section 5.1)
    "blocksize": 256,
    "input_device": None,       # None = not yet configured
    "output_device": None,      # None = not yet configured
    "asio": False,
    "autostart": False,
    "run_on_startup": False,    # Register app in Windows startup (HKCU run key)
    "last_preset": "",
    "max_autosaves": 0,         # 0 = unlimited
    "exclusive_mode": False,    # WASAPI exclusive mode; False = shared
    "vst3_dir":      VST3_DIR,
    "presets_dir":   PRESETS_DIR,
    "autosaves_dir": AUTOSAVES_DIR,
    "hotkeys": {"mute": "", "start": "", "stop": "", "presets": {}},
    "gauge_theme": "classic",
}

# Sample rates shown in the settings UI as override options
SUPPORTED_SAMPLE_RATES = [44100, 48000, 88200, 96000]

# Block sizes exposed in settings (Section 5.2; 128 proven, 256 default)
SUPPORTED_BLOCK_SIZES = [64, 128, 256, 512, 1024]

# VB-Cable name fragments used for detection (Section 8.2)
VBCABLE_NAME_FRAGMENTS = ("CABLE", "VB-Audio")

# Preferred host API name fragment for VB-Cable and mic selection
PREFERRED_HOST_API = "WASAPI"
