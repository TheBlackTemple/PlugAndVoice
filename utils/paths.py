"""
utils/paths.py — Canonical path resolution for PlugAndVoice.

THE PROBLEM
-----------
When running from source, all relative paths anchor to the repo root.
When packaged with PyInstaller --onedir, the .exe lives inside the bundle
folder and sys._MEIPASS points to an extracted tree for bundled *assets*
(icon, etc.) — but user data (presets, session, logs) must live next to
the .exe so the user can find and back them up.

TWO ROOTS, TWO PURPOSES
------------------------
  APP_ROOT   — where the .exe (or main.py in dev) lives.
               Use for runtime user data: user_data/, presets/, vst3/.
               These paths survive across updates and are user-accessible.

  ASSET_ROOT — where read-only bundled assets live.
               Dev:    same as APP_ROOT (assets sit in the repo).
               Bundle: sys._MEIPASS (PyInstaller extraction dir).
               Use for: resources/icon.ico and anything in spec datas.

USAGE
-----
    from utils.paths import app_path, asset_path

    icon   = asset_path("resources", "icon.ico")   # bundled read-only asset
    log    = app_path("user_data", "host.log")      # runtime user data
    preset = app_path("presets", "my_preset.json")  # runtime user data
"""

import os
import sys


def _resolve_app_root() -> str:
    """
    Return the directory that anchors all runtime/user-data paths.

    Frozen (PyInstaller):  directory containing the .exe
    Dev (plain Python):    repo root — the directory that contains main.py,
                           which is always one level above this file
                           (repo_root/utils/paths.py).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # utils/paths.py → go up two levels: utils/ → repo root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_asset_root() -> str:
    """
    Return the directory that anchors bundled read-only assets.

    Frozen: sys._MEIPASS  (PyInstaller unpacks datas here)
    Dev:    same as APP_ROOT (assets live in the repo)
    """
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return _resolve_app_root()


# Computed once at import time.
APP_ROOT: str   = _resolve_app_root()
ASSET_ROOT: str = _resolve_asset_root()


def app_path(*parts: str) -> str:
    """
    Resolve a path relative to APP_ROOT (next to the .exe / repo root).
    Use for anything written or read at runtime: user_data/, presets/, vst3/.

        app_path("user_data", "host.log")
        app_path("presets")
    """
    return os.path.join(APP_ROOT, *parts)


def asset_path(*parts: str) -> str:
    """
    Resolve a path relative to ASSET_ROOT (bundled read-only assets).
    Use for: resources/icon.ico, anything added via spec datas.

        asset_path("resources", "icon.ico")
    """
    return os.path.join(ASSET_ROOT, *parts)
