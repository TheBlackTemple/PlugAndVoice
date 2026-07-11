# PlugAndVoice.spec — PyInstaller --onedir build
#
# Place this file at the repo root (alongside main.py).
#
# Build:
#   pyinstaller PlugAndVoice.spec
#
# Output:
#   dist/PlugAndVoice/           ← zip this for distribution
#     PlugAndVoice.exe
#     resources/icon.ico
#     _internal/                 ← PyInstaller internals + collected libs
#
# user_data/, presets/, and vst3/ are NOT bundled — they live next to
# the .exe as ordinary folders. The app creates them via ensure_dirs()
# on first launch, so no manual stub creation is needed.

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)   # set by PyInstaller to the directory of this spec

# ── Collect pedalboard ────────────────────────────────────────────────────────
# pedalboard ships native extensions + its own bundled VST3 host.
# collect_all pulls in binaries, datas, and hidden imports in one call.
pb_datas, pb_binaries, pb_hiddenimports = collect_all("pedalboard")

# ── Collect sounddevice / PortAudio ──────────────────────────────────────────
sd_datas, sd_binaries, sd_hiddenimports = collect_all("sounddevice")

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[
        *pb_binaries,
        *sd_binaries,
    ],
    datas=[
        # Bundled read-only asset — lands in _MEIPASS; asset_path() resolves it
        (str(ROOT / "resources" / "icon.ico"), "resources"),

        *pb_datas,
        *sd_datas,
    ],
    hiddenimports=[
        # stdlib — sometimes missed by the analyser on Windows
        "winreg",

        # Your package roots
        "engine",
        "gui",
        "persistence",
        "settings",
        "utils",
        "utils.paths",
        "utils.startup",

        *pb_hiddenimports,
        *sd_hiddenimports,
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy.distutils",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PlugAndVoice",
    debug=False,
    strip=False,
    upx=True,
    console=False,      # no terminal window; flip to True to debug silent crashes
    icon=str(ROOT / "resources" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        # UPX can corrupt native audio extensions — exclude them
        "portaudio*.dll",
        "_pedalboard*.pyd",
        "_sounddevice*.pyd",
    ],
    name="PlugAndVoice",
)
