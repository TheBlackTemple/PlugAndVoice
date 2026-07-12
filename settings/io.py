"""
io.py — settings persistence and application logging setup.

Responsibilities:
  - Load settings from JSON; generate with defaults if file is missing or corrupt.
  - Save settings atomically (write temp → rename, keeps a .bak on overwrite).
  - Set up the root logger to write to ./host.log (Section 9, Module 2).
  - Ensure required directories exist (./vst3, ./presets).

Public API:
  setup_logging()            — call once at application startup, before anything else.
  load_settings() -> dict    — returns a validated, fully-populated settings dict.
  save_settings(d: dict)     — persists d to disk atomically.
  ensure_dirs()              — create ./vst3, ./presets if absent.
"""

import json
import logging
import os
import shutil
import tempfile

from .defaults import (
    AUTOSAVES_DIR,
    DEFAULTS,
    LOG_PATH,
    PRESETS_DIR,
    SETTINGS_PATH,
    SETTINGS_VERSION,
    VST3_DIR,
    USERDATA_PATH,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(level: int = logging.DEBUG) -> None:
    """
    Configure the root logger:
      - File handler  → LOG_PATH (DEBUG and above, appended across sessions)
      - Stream handler → stderr (INFO and above, for CLI harness visibility)

    Call this once, before any other module imports that use logging.
    """
    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — always DEBUG
    try:
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        # Don't crash if log path is unwritable; console handler still works.
        print(f"[WARNING] Could not open log file {LOG_PATH!r}: {e}")

    # Console handler — INFO+
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    log.debug("Logging initialised. Log file: %s", os.path.abspath(LOG_PATH))


# ---------------------------------------------------------------------------
# Directory bootstrap
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    """Create vst3, presets, and autosaves directories if they don't exist."""
    for d in (VST3_DIR, PRESETS_DIR, AUTOSAVES_DIR, USERDATA_PATH):
        os.makedirs(d, exist_ok=True)
        log.debug("Directory ensured: %s", os.path.abspath(d))


# ---------------------------------------------------------------------------
# Settings load
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    """
    Load settings from SETTINGS_PATH.

    - If the file does not exist, generate it from DEFAULTS and return.
    - If the file is corrupt (invalid JSON or wrong version), log a warning,
      rename the bad file to .corrupt, generate fresh defaults, and return.
    - Missing keys are filled in from DEFAULTS (forward-compatibility).

    Returns a fully-populated dict; never raises.
    """
    if not os.path.exists(SETTINGS_PATH):
        log.info("Settings file not found — generating defaults at %s", SETTINGS_PATH)
        settings = dict(DEFAULTS)
        _write(settings)
        return settings

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Settings file unreadable (%s) — resetting to defaults.", e)
        _quarantine(SETTINGS_PATH)
        settings = dict(DEFAULTS)
        _write(settings)
        return settings

    if not isinstance(loaded, dict):
        log.warning("Settings file has unexpected structure — resetting to defaults.")
        _quarantine(SETTINGS_PATH)
        settings = dict(DEFAULTS)
        _write(settings)
        return settings

    version = loaded.get("version")
    if version != SETTINGS_VERSION:
        log.warning(
            "Settings version mismatch (got %r, expected %d) — "
            "filling missing keys from defaults.",
            version, SETTINGS_VERSION,
        )

    # Merge: start from defaults, overlay loaded values for known keys.
    settings = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in loaded:
            settings[key] = loaded[key]

    # Always stamp the current version.
    settings["version"] = SETTINGS_VERSION

    log.debug("Settings loaded from %s", SETTINGS_PATH)
    return settings


# ---------------------------------------------------------------------------
# Settings save
# ---------------------------------------------------------------------------

def save_settings(settings: dict) -> None:
    """
    Persist settings to SETTINGS_PATH atomically.

    Strategy:
      1. Write to a temp file in the same directory.
      2. If the target already exists, copy it to TARGET.bak.
      3. Rename temp → target (atomic on POSIX; best-effort on Windows).

    Never raises — logs errors instead. (Settings loss is survivable since
    the file regenerates with defaults on next launch.)
    """
    settings = dict(settings)
    settings["version"] = SETTINGS_VERSION
    _write(settings)


def _write(settings: dict) -> None:
    target = SETTINGS_PATH
    target_dir = os.path.dirname(os.path.abspath(target)) or "."

    try:
        # Write to a sibling temp file
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
                f.write("\n")
        except Exception:
            os.unlink(tmp_path)
            raise

        # Back up the existing file
        if os.path.exists(target):
            bak = target + ".bak"
            shutil.copy2(target, bak)

        # Atomic rename
        os.replace(tmp_path, target)
        log.debug("Settings saved to %s", os.path.abspath(target))

    except OSError as e:
        log.error("Failed to save settings: %s", e)


def _quarantine(path: str) -> None:
    """Rename a corrupt settings file to .corrupt so it isn't re-read."""
    corrupt_path = path + ".corrupt"
    try:
        os.replace(path, corrupt_path)
        log.info("Corrupt settings file moved to %s", corrupt_path)
    except OSError as e:
        log.warning("Could not quarantine corrupt settings file: %s", e)
