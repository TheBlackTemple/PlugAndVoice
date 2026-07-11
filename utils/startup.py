"""
utils/startup.py — Windows startup registry helper.

Manages the HKCU run key so PlugAndVoice launches with Windows.
HKCU requires no UAC elevation.

The stored value is always sys.executable, which is:
  Dev:    python.exe  (harmless — just don't ship with the box ticked)
  Bundle: PlugAndVoice.exe  (correct)

The path is never cached in settings — it's always read from sys.executable
at the moment of writing, so moving the folder and re-saving self-heals it.
"""

import logging
import sys

log = logging.getLogger(__name__)

_REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "PlugAndVoice"


def set_run_on_startup(enable: bool) -> bool:
    """
    Add or remove the Windows startup registry entry.
    Returns True on success, False on failure or non-Windows.
    """
    try:
        import winreg
    except ImportError:
        log.warning("winreg not available — startup toggle is Windows-only.")
        return False

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _REG_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enable:
                exe = f'"{sys.executable}"'
                winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, exe)
                log.info("Startup entry added: %s", exe)
            else:
                try:
                    winreg.DeleteValue(key, _APP_NAME)
                    log.info("Startup entry removed.")
                except FileNotFoundError:
                    pass  # already absent — fine
        return True

    except OSError as exc:
        log.error("Failed to update startup registry entry: %s", exc)
        return False


def is_run_on_startup() -> bool:
    """
    Return True if the startup registry entry currently exists.
    Used to sync the checkbox state when settings opens.
    """
    try:
        import winreg
    except ImportError:
        return False

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _REG_KEY,
            0,
            winreg.KEY_READ,
        ) as key:
            winreg.QueryValueEx(key, _APP_NAME)
            return True
    except (OSError, FileNotFoundError):
        return False
