"""
styles.py — visual tokens, stylesheet, and the dB gauge widget.

Aesthetic direction: rack hardware / signal chain. Dark gunmetal panels,
amber VU needle colour for levels, red clip indicators, monospaced readouts.
One signature element: the dB gauge uses a segmented bar that shifts colour
at -18 dBFS (green → amber → red), mirroring real hardware VU segments.
"""

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
"""


# ── dB gauge widget ───────────────────────────────────────────────────────────

class DbGauge(QWidget):
    """
    Vertical segmented dB bar.

    Segments shift colour at fixed thresholds:
      -∞ … -18 dBFS  → green
      -18 … -6 dBFS  → amber
      -6 … 0 dBFS    → red
      ≥ 0 dBFS       → clip latch (solid bright red, resets on click)

    update_level(db: float) — call from the GUI thread (QTimer poll).
    """

    FLOOR_DB = -60.0
    CLIP_DB  = 0.0

    THRESHOLDS = [  # (upper_db, colour)
        (-18.0, C_GAUGE_LOW),
        (-6.0,  C_GAUGE_MID),
        (0.0,   C_GAUGE_HIGH),
    ]

    def __init__(self, parent=None, width=8, height=80):
        super().__init__(parent)
        self._db = self.FLOOR_DB
        self._clipped = False
        self.setFixedSize(width, height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def update_level(self, db: float) -> None:
        self._db = max(db, self.FLOOR_DB)
        if db >= self.CLIP_DB:
            self._clipped = True
        self.update()

    def reset_clip(self) -> None:
        self._clipped = False
        self.update()

    def mousePressEvent(self, event):
        self.reset_clip()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QColor(C_GAUGE_BG))

        if self._clipped:
            p.fillRect(0, 0, w, h, QColor(C_GAUGE_CLIP))
            return

        db = self._db
        fill_frac = (db - self.FLOOR_DB) / (self.CLIP_DB - self.FLOOR_DB)
        fill_frac = max(0.0, min(1.0, fill_frac))
        fill_px = int(fill_frac * h)

        if fill_px <= 0:
            return

        # Draw fill from bottom up, split by threshold bands
        bottom = h
        remaining = fill_px

        for i, (upper_db, colour) in enumerate(reversed(self.THRESHOLDS)):
            lower_db = self.THRESHOLDS[len(self.THRESHOLDS) - i - 2][0] if i < len(self.THRESHOLDS) - 1 else self.FLOOR_DB
            band_frac = (upper_db - lower_db) / (self.CLIP_DB - self.FLOOR_DB)
            band_px = int(band_frac * h)
            draw_px = min(remaining, band_px)
            if draw_px > 0:
                top = bottom - draw_px
                p.fillRect(0, top, w, draw_px, QColor(colour))
                bottom = top
                remaining -= draw_px
                if remaining <= 0:
                    break

        p.end()
