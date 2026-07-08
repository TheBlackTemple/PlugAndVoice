"""
hotkeys_tab.py — Hotkeys settings tab for SettingsView.

Responsibilities:
  - KeyCaptureEdit: a QWidget subclass that captures key combos on demand,
    formats them as strings ("Ctrl+M", "F5", etc.), and rejects unsafe keys.
  - HotkeysTab: the tab widget itself, with:
      - Fixed rows for mute, start, stop
      - A dynamic list of preset → key bindings (add / remove)
      - Collision dedup on write_settings() — last writer wins per key string
  - Provides read_settings(d) / write_settings(d) so SettingsView can treat it
    like any other tab (populate on open, harvest on Apply).

Keys blocked from capture (unsafe or reserved):
  Escape, Delete, Backspace, Return, Enter, Tab,
  bare modifier keys (Shift, Ctrl, Alt, Meta/Win).

Preset list is loaded once at construction time from list_presets().
It does not update dynamically while the dialog is open.
"""

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

log = logging.getLogger(__name__)

# Keys that must never be captured — they serve navigation / system roles.
_BLOCKED_KEYS = {
    Qt.Key_Escape, Qt.Key_Delete, Qt.Key_Backspace,
    Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab, Qt.Key_Backtab,
    # Bare modifiers — useless alone and confusing to display.
    Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta,
    Qt.Key_AltGr, Qt.Key_CapsLock, Qt.Key_NumLock, Qt.Key_ScrollLock,
}


# ── Key capture widget ────────────────────────────────────────────────────────

class KeyCaptureEdit(QWidget):
    """
    Read-only display label + "Set" toggle + "Clear" button.

    Click "Set" to enter capture mode. The next key combo pressed is recorded
    and displayed as a portable key string ("Ctrl+M", "F5", "Shift+F9", etc.).
    Blocked keys are silently ignored while capturing. Escape cancels capture
    without changing the current binding.

    Value is exposed via .key_string / .set_key_string(s).
    """

    _PLACEHOLDER  = "— unbound —"
    _STYLE_IDLE   = (
        "QLabel { background: #1a1d22; border: 1px solid #2e3138; "
        "padding: 3px 6px; border-radius: 3px; font-family: monospace; }"
    )
    _STYLE_ACTIVE = (
        "QLabel { background: #1a2a1a; border: 1px solid #4caf50; "
        "padding: 3px 6px; border-radius: 3px; font-family: monospace; }"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._key_string = ""
        self._capturing  = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._display = QLabel(self._PLACEHOLDER)
        self._display.setMinimumWidth(120)
        self._display.setStyleSheet(self._STYLE_IDLE)
        layout.addWidget(self._display)

        self._set_btn = QPushButton("Set")
        self._set_btn.setFixedWidth(40)
        self._set_btn.setCheckable(True)
        self._set_btn.clicked.connect(self._on_set_clicked)
        layout.addWidget(self._set_btn)

        self._clear_btn = QPushButton("✕")
        self._clear_btn.setFixedWidth(30)
        self._clear_btn.clicked.connect(self.clear)
        layout.addWidget(self._clear_btn)

        self.setFocusPolicy(Qt.StrongFocus)

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def key_string(self) -> str:
        return self._key_string

    def set_key_string(self, s: str) -> None:
        self._key_string = s or ""
        self._display.setText(s if s else self._PLACEHOLDER)

    def clear(self) -> None:
        self._cancel_capture()
        self.set_key_string("")

    # ── Capture state ─────────────────────────────────────────────────────────

    def _on_set_clicked(self) -> None:
        if self._set_btn.isChecked():
            self._start_capture()
        else:
            self._cancel_capture()

    def _start_capture(self) -> None:
        self._capturing = True
        self._display.setText("… press a key …")
        self._display.setStyleSheet(self._STYLE_ACTIVE)
        self._set_btn.setChecked(True)
        self.setFocus()

    def _cancel_capture(self) -> None:
        self._capturing = False
        self._set_btn.setChecked(False)
        self._display.setText(self._key_string if self._key_string else self._PLACEHOLDER)
        self._display.setStyleSheet(self._STYLE_IDLE)

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return

        key = event.key()

        # Escape always cancels without changing the binding.
        if key == Qt.Key_Escape:
            self._cancel_capture()
            return

        # Other blocked keys: stay in capture mode, wait for a valid key.
        if key in _BLOCKED_KEYS:
            return

        combo = QKeySequence(event.keyCombination())
        text  = combo.toString(QKeySequence.PortableText)
        if not text:
            return

        self.set_key_string(text)
        self._cancel_capture()

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._cancel_capture()
        super().focusOutEvent(event)


# ── Hotkeys tab ───────────────────────────────────────────────────────────────

class HotkeysTab(QWidget):
    """
    Tab widget embedded in SettingsView's QTabWidget.

    Fixed bindings: mute toggle, start engine, stop engine.
    Dynamic bindings: one row per (preset_name → key) pair.

    read_settings(d)  — populate all widgets from settings dict
    write_settings(d) — harvest all widgets back into settings dict,
                        performing collision dedup (last row wins per key).
    """

    def __init__(self, preset_names: list[str], parent=None):
        super().__init__(parent)
        self._preset_names = preset_names   # frozen at construction time
        self._build_ui()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        #root.setSpacing(10)
        #root.setContentsMargins(0, 8, 0, 0)

        # ── Fixed bindings ────────────────────────────────────────────────────
        fixed_group = QGroupBox("ENGINE CONTROLS")
        fixed_form  = QFormLayout(fixed_group)
        fixed_form.setLabelAlignment(Qt.AlignRight)
        #fixed_form.setSpacing(8)

        self._mute_capture  = KeyCaptureEdit()
        self._start_capture = KeyCaptureEdit()
        self._stop_capture  = KeyCaptureEdit()

        fixed_form.addRow("Mute toggle:",  self._mute_capture)
        fixed_form.addRow("Start engine:", self._start_capture)
        fixed_form.addRow("Stop engine:",  self._stop_capture)

        root.addWidget(fixed_group)

        # ── Preset bindings ───────────────────────────────────────────────────
        preset_group  = QGroupBox("PRESET BINDINGS")
        preset_layout = QVBoxLayout(preset_group)
        preset_layout.setSpacing(6)

        hint = QLabel(
            "Hotkey loads the preset immediately without confirmation. "
            "If the preset no longer exists at press time it is silently ignored. "
            "Keybinds are unique across applications. " 
            "If discord already registered a specific bind, it will not trigger here."
        )
        hint.setProperty("class", "hint")
        hint.setWordWrap(True)
        preset_layout.addWidget(hint)

        self._preset_list = QListWidget()
        self._preset_list.setSpacing(2)
        self._preset_list.setFixedHeight(180)
        preset_layout.addWidget(self._preset_list)

        add_row = QHBoxLayout()
        add_row.addStretch()

        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(160)
        for name in self._preset_names:
            self._preset_combo.addItem(name)
        if not self._preset_names:
            self._preset_combo.setEnabled(False)
        add_row.addWidget(self._preset_combo)

        self._add_preset_btn = QPushButton("+ Add binding")
        self._add_preset_btn.setEnabled(bool(self._preset_names))
        self._add_preset_btn.clicked.connect(self._on_add_preset_binding)
        add_row.addWidget(self._add_preset_btn)

        self._remove_preset_btn = QPushButton("Remove")
        self._remove_preset_btn.setEnabled(False)
        self._remove_preset_btn.clicked.connect(self._on_remove_preset_binding)
        add_row.addWidget(self._remove_preset_btn)

        preset_layout.addLayout(add_row)
        root.addWidget(preset_group)

        self._preset_list.itemSelectionChanged.connect(
            lambda: self._remove_preset_btn.setEnabled(
                bool(self._preset_list.selectedItems())
            )
        )

        root.addStretch()

    # ── Preset binding list helpers ───────────────────────────────────────────

    def _on_add_preset_binding(self) -> None:
        name = self._preset_combo.currentText()
        if not name:
            return
        self._add_preset_row(name, "")

    def _on_remove_preset_binding(self) -> None:
        for item in self._preset_list.selectedItems():
            self._preset_list.takeItem(self._preset_list.row(item))

    def _add_preset_row(self, preset_name: str, key_string: str) -> None:
        item   = QListWidgetItem()
        widget = _PresetBindingRow(preset_name, key_string)
        item.setSizeHint(widget.sizeHint() + QSize(0, 12))
        self._preset_list.addItem(item)
        self._preset_list.setItemWidget(item, widget)

    # ── Settings interface ────────────────────────────────────────────────────

    def read_settings(self, settings: dict) -> None:
        """Populate widgets from settings["hotkeys"]."""
        hk = settings.get("hotkeys") or {}

        self._mute_capture.set_key_string(hk.get("mute",  ""))
        self._start_capture.set_key_string(hk.get("start", ""))
        self._stop_capture.set_key_string(hk.get("stop",  ""))

        self._preset_list.clear()
        for key_str, preset_name in (hk.get("presets") or {}).items():
            self._add_preset_row(preset_name, key_str)

    def write_settings(self, settings: dict) -> None:
        """
        Harvest widgets into settings["hotkeys"].

        Collision dedup: if the same key string appears more than once across
        all bindings (fixed + preset rows), the last occurrence wins. Fixed
        bindings are processed first so a preset row added below them can
        override — last entry the user touched is at the bottom.
        """
        # Ordered list of (key_string, action_tag) — fixed first, presets after.
        pairs: list[tuple[str, str]] = []

        for capture, tag in (
            (self._mute_capture,  "mute"),
            (self._start_capture, "start"),
            (self._stop_capture,  "stop"),
        ):
            ks = capture.key_string
            if ks:
                pairs.append((ks, tag))

        for i in range(self._preset_list.count()):
            item   = self._preset_list.item(i)
            widget = self._preset_list.itemWidget(item)
            if isinstance(widget, _PresetBindingRow):
                ks   = widget.key_string
                name = widget.preset_name
                if ks and name:
                    pairs.append((ks, f"preset:{name}"))

        # Dedup: last tag for each key string wins.
        seen: dict[str, str] = {}
        for ks, tag in pairs:
            if ks in seen:
                log.warning(
                    "Hotkey collision on %r: was bound to %r, reassigned to %r.",
                    ks, seen[ks], tag,
                )
            seen[ks] = tag

        mute_key  = next((ks for ks, tag in seen.items() if tag == "mute"),  "")
        start_key = next((ks for ks, tag in seen.items() if tag == "start"), "")
        stop_key  = next((ks for ks, tag in seen.items() if tag == "stop"),  "")
        preset_bindings = {
            ks: tag[len("preset:"):] for ks, tag in seen.items()
            if tag.startswith("preset:")
        }

        settings["hotkeys"] = {
            "mute":    mute_key,
            "start":   start_key,
            "stop":    stop_key,
            "presets": preset_bindings,
        }


# ── Preset binding row widget ─────────────────────────────────────────────────

class _PresetBindingRow(QWidget):
    """
    One item in the preset binding QListWidget.
    Preset name label (fixed) + KeyCaptureEdit.
    """

    def __init__(self, preset_name: str, key_string: str, parent=None):
        super().__init__(parent)
        self._preset_name = preset_name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        #layout.setSpacing(8)

        lbl = QLabel(preset_name)
        lbl.setMinimumWidth(120)
        lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(lbl)

        self._capture = KeyCaptureEdit()
        self._capture.set_key_string(key_string)
        layout.addWidget(self._capture)

    @property
    def preset_name(self) -> str:
        return self._preset_name

    @property
    def key_string(self) -> str:
        return self._capture.key_string
