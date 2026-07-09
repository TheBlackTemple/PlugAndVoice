"""
styles.py — visual tokens, stylesheet, and the dB gauge widget.

Aesthetic direction: rack hardware / signal chain. Dark gunmetal panels,
amber VU needle colour for levels, red clip indicators, monospaced readouts.
One signature element: the dB gauge uses a segmented bar that shifts colour
at -18 dBFS (green → amber → red), mirroring real hardware VU segments.

Theme system
------------
``GAUGE_THEMES`` is a registry of named ``GaugeTheme`` objects.  Each theme
defines three segment colours (low / mid / high), a clip colour, and an unlit
background.  ``DbGauge.set_theme()`` swaps the active theme at runtime with no
restart required.

Adding a new theme: append an entry to ``GAUGE_THEMES`` and it will appear
automatically in the Themes tab of SettingsView.

DEFAULT_GAUGE_THEME is the key used when no preference is saved.
"""

from dataclasses import dataclass
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QLinearGradient
from PySide6.QtWidgets import QWidget, QSizePolicy


# ── Colour tokens ─────────────────────────────────────────────────────────────

# Panels / backgrounds
C_BG_DEEP    = "#111214"   # main window / deepest background
C_BG_PANEL   = "#1a1c1f"   # card / slot background
C_BG_RAISED  = "#22252a"   # raised element (header bar, footer)
C_BORDER     = "#2e3138"   # dividers and slot outlines
C_BORDER_LO  = "#1e2025"   # subtle inner borders

# Text
C_TEXT       = "#d4d8de"   # body text
C_TEXT_DIM   = "#6b7280"   # labels, inactive text
C_TEXT_WARN  = "#e8a030"   # inline warning text (amber)
C_TEXT_ERR   = "#e05050"   # inline error / device-lost text

# Signal-chain accent colours
C_SIGNAL     = "#3b8fd4"   # active signal path (blue)
C_SIGNAL_DIM = "#1f4a70"   # dimmed signal

# Gauge segments
C_GAUGE_LOW  = "#2ecc71"   # -∞ … -18 dBFS  (green)
C_GAUGE_MID  = "#e8a030"   # -18 … -6 dBFS   (amber)
C_GAUGE_HIGH = "#e05050"   # -6 … 0 dBFS     (red)
C_GAUGE_CLIP = "#ff2222"   # ≥ 0 dBFS clip indicator
C_GAUGE_BG   = "#0d0f11"   # unlit gauge background

# Interactive
C_BTN_START  = "#2e7d32"
C_BTN_STOP   = "#7b1f1f"
C_BTN_MUTE   = "#5a3e00"
C_BTN_MUTED  = "#e8a030"
C_BTN_BYPASS = "#1a4a6e"
C_BTN_BYPASSED = "#3b8fd4"
C_ACCENT     = "#3b8fd4"


# ── Gauge theme system ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GaugeTheme:
    """
    Immutable colour palette for a DbGauge instance.

    Segment boundaries (-18 dBFS and -6 dBFS) are fixed by the hardware VU
    convention baked into DbGauge.THRESHOLDS; only colours vary per theme.

    Attributes
    ----------
    name        Human-readable label shown in the Themes tab.
    description One-line hint shown below the theme picker.
    low         Colour for -∞ … -18 dBFS.
    mid         Colour for -18 … -6 dBFS.
    high        Colour for -6 … 0 dBFS.
    clip        Colour for the clip-latch indicator (≥ 0 dBFS).
    bg          Unlit segment background.
    """
    name:        str
    description: str
    low:         str
    mid:         str
    high:        str
    clip:        str
    bg:          str


# Registry — order determines display order in SettingsView.
# Key = settings value stored in preferences.
GAUGE_THEMES: dict[str, GaugeTheme] = {

    "classic": GaugeTheme(
        name="Classic VU",
        description="Green → amber → red. The hardware VU standard.",
        low=C_GAUGE_LOW,    # #2ecc71
        mid=C_GAUGE_MID,    # #e8a030
        high=C_GAUGE_HIGH,  # #e05050
        clip=C_GAUGE_CLIP,
        bg=C_GAUGE_BG,
    ),

    "phosphor": GaugeTheme(
        name="Phosphor",
        description="Vintage green phosphor CRT. Oscilloscope energy.",
        low="#00a86b",
        mid="#39ff88",
        high="#b8ffdc",
        clip="#eaffee",
        bg="#020d06",
    ),

    "forge": GaugeTheme(
        name="Forge",
        description="Black-body radiation. Cold iron → orange heat → white-hot.",
        low="#3a0a00",      # near-black red, barely visible at rest
        mid="#c43000",      # deep forge orange
        high="#ff8c00",     # bright orange-amber
        clip="#fff4c2",     # white-hot near-white
        bg="#080200",
    ),

    "broadcast": GaugeTheme(
        name="Broadcast",
        description="PPM-style: peak programme meters from BBC/EBU console tradition.",
        low="#005f9e",      # BBC corporate blue
        mid="#00a0c8",      # IEC teal
        high="#f0e030",     # PPM yellow — not red, that's the point
        clip="#ff4040",     # clip breaks the pattern intentionally
        bg="#060c12",
    ),

    "abyss": GaugeTheme(
        name="Abyss",
        description="Deep ocean bioluminescence. Near-black to electric cyan.",
        low="#002a3a",      # deep ocean dark teal
        mid="#006e7a",      # bioluminescent mid
        high="#00e8cc",     # electric cyan surface break
        clip="#a0fff5",     # overdriven bloom
        bg="#00080c",
    ),

    "sodium": GaugeTheme(
        name="Sodium",
        description="Sodium vapour streetlight ramp. Warm monochromatic amber.",
        low="#2a1400",      # near-black amber
        mid="#8a4800",      # mid sodium orange
        high="#ffb000",     # peak sodium yellow
        clip="#fff5cc",     # lamp bloom
        bg="#0a0500",
    ),

    "lufs": GaugeTheme(
        name="LUFS",
        description="EBU R 128 loudness colour convention. Familiar to mastering engineers.",
        low="#1a7a40",      # integrated green
        mid="#c8a000",      # momentary caution yellow
        high="#c83200",     # true peak warning
        clip="#ff2020",     # over true peak
        bg="#0a0c0a",
    ),

    "plasma": GaugeTheme(
        name="Plasma",
        description="Tesla coil discharge. Deep violet ionisation to white arc.",
        low="#1a0038",      # near-black violet
        mid="#7020c0",      # ionised purple
        high="#d060ff",     # arc magenta
        clip="#fff0ff",     # white discharge bloom
        bg="#06000c",
    ),
}

DEFAULT_GAUGE_THEME = "classic"


# ── Global stylesheet ─────────────────────────────────────────────────────────

STYLESHEET = f"""
QWidget {{
    background-color: {C_BG_DEEP};
    color: {C_TEXT};
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 11px;
}}

QMainWindow, QDialog {{
    background-color: {C_BG_DEEP};
}}

/* ── Labels ── */
QLabel {{
    color: {C_TEXT};
    background: transparent;
}}
QLabel[class="dim"] {{
    color: {C_TEXT_DIM};
    font-size: 10px;
}}
QLabel[class="hint"] {{
    color: {C_TEXT_DIM};
    font-size: 10px;
    font-style: italic;
}}
QLabel[class="warn"] {{
    color: {C_TEXT_WARN};
}}
QLabel[class="err"] {{
    color: {C_TEXT_ERR};
    font-weight: bold;
}}
QLabel[class="section"] {{
    color: {C_TEXT_DIM};
    font-size: 9px;
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QLabel[class="mono"] {{
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
    color: {C_TEXT};
}}
QLabel[class="readout"] {{
    font-family: "Consolas", "Courier New", monospace;
    font-size: 12px;
    color: {C_SIGNAL};
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {C_BG_RAISED};
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    border-radius: 3px;
    padding: 4px 10px;
    min-height: 22px;
}}
QPushButton:hover {{
    border-color: {C_ACCENT};
    color: #ffffff;
}}
QPushButton:pressed {{
    background-color: {C_BG_PANEL};
}}
QPushButton:disabled {{
    color: {C_TEXT_DIM};
    border-color: {C_BORDER_LO};
}}
QPushButton[class="start"] {{
    background-color: {C_BTN_START};
    border-color: #3d9140;
    font-weight: bold;
}}
QPushButton[class="stop"] {{
    background-color: {C_BTN_STOP};
    border-color: #9d3030;
    font-weight: bold;
}}
QPushButton[class="mute"] {{
    background-color: {C_BTN_MUTE};
    border-color: #8a5c00;
}}
QPushButton[class="muted"] {{
    background-color: {C_BTN_MUTED};
    color: #1a1000;
    border-color: #b07820;
    font-weight: bold;
}}
QPushButton[class="danger"] {{
    color: {C_TEXT_ERR};
    border-color: {C_TEXT_ERR};
}}

/* ── Dropdowns / ComboBox ── */
QComboBox {{
    background-color: {C_BG_RAISED};
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    border-radius: 3px;
    padding: 3px 8px;
    min-height: 22px;
}}
QComboBox:hover {{
    border-color: {C_ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {C_BG_RAISED};
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    selection-background-color: {C_SIGNAL_DIM};
}}

/* ── GroupBox / panels ── */
QGroupBox {{
    background-color: {C_BG_PANEL};
    border: 1px solid {C_BORDER};
    border-radius: 4px;
    margin-top: 14px;
    padding: 6px 8px 8px 8px;
    font-size: 10px;
    color: {C_TEXT_DIM};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    top: 2px;
    color: {C_TEXT_DIM};
    letter-spacing: 1px;
}}

/* ── CheckBox ── */
QCheckBox {{
    color: {C_TEXT};
    spacing: 6px;
    background-color: {C_BG_PANEL};
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {C_BORDER};
    border-radius: 2px;
    background: {C_BG_RAISED};
}}
QCheckBox::indicator:checked {{
    background: {C_ACCENT};
    border-color: {C_ACCENT};
}}

/* ── Scrollbars ── */
QScrollBar:vertical {{
    background: {C_BG_PANEL};
    width: 6px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {C_BORDER};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* ── Text inputs ── */
QLineEdit {{
    background-color: {C_BG_RAISED};
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    border-radius: 3px;
    padding: 3px 6px;
    min-height: 22px;
}}
QLineEdit:hover {{
    border-color: {C_ACCENT};
}}
QLineEdit:focus {{
    border-color: {C_ACCENT};
}}
QLineEdit:disabled {{
    color: {C_TEXT_DIM};
    border-color: {C_BORDER_LO};
}}
QLineEdit[class="hint"] {{
    color: {C_TEXT_DIM};
}}

/* ── Spin box ── */
QSpinBox {{
    background-color: {C_BG_RAISED};
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    border-radius: 3px;
    padding: 2px 4px;
}}

/* ── Separators ── */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {{
    color: {C_BORDER};
}}

/* ── Tabs ── */
QWidget#tabPage {{ 
    background-color: {C_BG_PANEL}; 
}}

QTabWidget::pane {{
    background-color: {C_BG_PANEL};
    border: 1px solid {C_BORDER};
    border-radius: 4px;
    padding: 10px;
}}
QTabWidget::tab-bar {{
    alignment: left;
}}
QTabBar::tab {{
    background-color: {C_BG_RAISED};
    color: {C_TEXT_DIM};
    border: 1px solid {C_BORDER};
    border-bottom: none;
    border-radius: 3px 3px 0 0;
    padding: 5px 14px;
    margin-right: 2px;
    font-size: 11px;
}}
QTabBar::tab:selected {{
    background-color: {C_BG_PANEL};
    color: {C_TEXT};
    border-bottom: 1px solid {C_BG_PANEL};
}}
QTabBar::tab:hover:!selected {{
    color: {C_TEXT};
    border-color: {C_ACCENT};
    border-bottom: none;
}}

/* ── Status / info bar ── */
QStatusBar {{
    background: {C_BG_RAISED};
    color: {C_TEXT_DIM};
    font-size: 10px;
}}

/* ── Message box ── */
QMessageBox {{
    background: {C_BG_PANEL};
}}

/* ── List Widget ── */
QListWidget, QListView {{
    background-color: {C_BG_PANEL};
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    border-radius: 4px;
    alternate-background-color: {C_BG_RAISED};
    outline: none;
}}
QListWidget::item, QListView::item {{
    padding: 4px 8px;
    border: none;
}}
QListWidget::item:alternate, QListView::item:alternate {{
    background-color: {C_BG_RAISED};
}}
QListWidget::item:selected, QListView::item:selected {{
    background-color: {C_SIGNAL_DIM};
    color: #ffffff;
}}
QListWidget::item:hover, QListView::item:hover {{
    background-color: {C_BG_DEEP};
}}
"""


# ── dB gauge widget ───────────────────────────────────────────────────────────

class DbGauge(QWidget):
    """
    Vertical segmented dB bar with swappable colour themes.

    Segments shift colour at fixed thresholds (hardware VU convention):
      -∞ … -18 dBFS  → theme.low
      -18 … -6 dBFS  → theme.mid
      -6 … 0 dBFS    → theme.high
      ≥ 0 dBFS       → clip latch (theme.clip, resets on click)

    Public API
    ----------
    update_level(db)  — call from the GUI thread (QTimer poll).
    set_theme(key)    — swap palette by GAUGE_THEMES key; repaints immediately.
    reset_clip()      — clear clip latch programmatically.
    """

    FLOOR_DB = -60.0
    CLIP_DB  = 0.0

    # Segment band boundaries (upper dB edge).  Colours are resolved from the
    # active theme at paint time so set_theme() never needs to touch this list.
    _BAND_UPPER_DB = [-18.0, -6.0, 0.0]

    def __init__(self, parent=None, width=8, height=80,
                 theme_key: str = DEFAULT_GAUGE_THEME):
        super().__init__(parent)
        self._db = self.FLOOR_DB
        self._clipped = False
        self._theme: GaugeTheme = GAUGE_THEMES.get(theme_key,
                                    GAUGE_THEMES[DEFAULT_GAUGE_THEME])
        self.setFixedSize(width, height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    # ── Theme control ─────────────────────────────────────────────────────────

    def set_theme(self, key: str) -> None:
        """Swap to a named theme from GAUGE_THEMES.  Unknown keys are ignored."""
        if key in GAUGE_THEMES:
            self._theme = GAUGE_THEMES[key]
            self.update()

    @property
    def theme_key(self) -> str:
        """Return the key of the currently active theme."""
        for k, v in GAUGE_THEMES.items():
            if v is self._theme:
                return k
        return DEFAULT_GAUGE_THEME

    # ── Level control ─────────────────────────────────────────────────────────

    def update_level(self, db: float) -> None:
        self._db = max(db, self.FLOOR_DB)

        # TODO: reimplement clipping with a delay, ignored for now
        # if db >= self.CLIP_DB:
        #     self._clipped = True
        self.update()

    def reset_clip(self) -> None:
        self._clipped = False
        self.update()

    def mousePressEvent(self, event):
        self.reset_clip()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        theme = self._theme

        p.fillRect(0, 0, w, h, QColor(theme.bg))

        # if self._clipped:
        #     p.fillRect(0, 0, w, h, QColor(theme.clip))
        #     return

        db = self._db
        fill_frac = (db - self.FLOOR_DB) / (self.CLIP_DB - self.FLOOR_DB)
        fill_frac = max(0.0, min(1.0, fill_frac))
        fill_px = int(fill_frac * h)

        if fill_px <= 0:
            return

        # Band colours follow THRESHOLDS order: low, mid, high
        band_colours = [theme.low, theme.mid, theme.high]

        bottom = h
        remaining = fill_px
        prev_lower_db = self.FLOOR_DB

        for upper_db, colour in zip(self._BAND_UPPER_DB, band_colours):
            band_frac = (upper_db - prev_lower_db) / (self.CLIP_DB - self.FLOOR_DB)
            band_px = int(band_frac * h)
            draw_px = min(remaining, band_px)
            if draw_px > 0:
                top = bottom - draw_px
                p.fillRect(0, top, w, draw_px, QColor(colour))
                bottom = top
                remaining -= draw_px
                if remaining <= 0:
                    break
            prev_lower_db = upper_db

        p.end()
