"""
settings_view.py — Settings / entry-point dialog.

Responsibilities (Section 12.1):
  - Device dropdowns (host-API-qualified entries, Section 8.1).
  - Sample rate and blocksize selectors.
  - Inline VB-Cable warning when no VB-Cable device found (Section 8.2).
  - Inline "Device not found" and "Device lost" flags (Section 8.6).
  - "Remember settings and autostart" checkbox.
  - Apply button: validate → save → emit accepted (caller triggers restart).
  - Never touches the engine directly; all decisions go through signals.

Signals:
  settings_applied(dict)   — emitted when user clicks Apply; payload is
                             the new settings dict ready for engine.start().
"""

import logging

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QHBoxLayout, QFrame,
)

from settings import (
    load_settings, save_settings,
    enumerate_devices, suggest_devices, find_device_by_name, validate_devices,
    vbcable_present, asio_available,
    SUPPORTED_SAMPLE_RATES, SUPPORTED_BLOCK_SIZES,
)
from .styles import C_TEXT_WARN, C_TEXT_ERR

log = logging.getLogger(__name__)


class SettingsView(QDialog):
    """
    Settings dialog — entry point on first run; accessible via button on main window.
    """

    settings_applied = Signal(dict)   # emitted with validated settings dict

    def __init__(self, parent=None, device_lost: bool = False):
        super().__init__(parent)
        self.setWindowTitle("MicHost — Settings")
        self.setMinimumWidth(520)
        self.setModal(True)

        self._devices = enumerate_devices()
        self._suggestions = suggest_devices(self._devices)
        self._current_settings = load_settings()
        self._device_lost = device_lost

        self._build_ui()
        self._populate()
        self._check_warnings()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # ── Device lost banner ────────────────────────────────────────────────
        if self._device_lost:
            lost_label = QLabel("⚠  A device became unavailable while streaming. "
                                "Re-select devices and apply to restart.")
            lost_label.setProperty("class", "err")
            lost_label.setWordWrap(True)
            root.addWidget(lost_label)
            self._add_separator(root)

        # ── Audio devices ─────────────────────────────────────────────────────
        dev_group = QGroupBox("AUDIO DEVICES")
        dev_form = QFormLayout(dev_group)
        dev_form.setLabelAlignment(Qt.AlignRight)
        dev_form.setSpacing(8)

        self._in_combo = QComboBox()
        self._in_warning = QLabel()
        self._in_warning.setProperty("class", "err")
        self._in_warning.setVisible(False)
        in_col = QVBoxLayout()
        in_col.setSpacing(2)
        in_col.addWidget(self._in_combo)
        in_col.addWidget(self._in_warning)
        dev_form.addRow("Input device:", in_col)

        self._out_combo = QComboBox()
        self._out_warning = QLabel()
        self._out_warning.setProperty("class", "err")
        self._out_warning.setVisible(False)
        out_col = QVBoxLayout()
        out_col.setSpacing(2)
        out_col.addWidget(self._out_combo)
        out_col.addWidget(self._out_warning)
        dev_form.addRow("Output device:", out_col)

        root.addWidget(dev_group)

        # ── VB-Cable warning ──────────────────────────────────────────────────
        self._vbcable_warn = QLabel(
            "⚠  No VB-Cable device found. Routing audio to a real output device "
            "may cause feedback. Confirm before starting the engine."
        )
        self._vbcable_warn.setProperty("class", "warn")
        self._vbcable_warn.setWordWrap(True)
        self._vbcable_warn.setVisible(False)
        root.addWidget(self._vbcable_warn)

        # ── Format ────────────────────────────────────────────────────────────
        fmt_group = QGroupBox("FORMAT")
        fmt_form = QFormLayout(fmt_group)
        fmt_form.setLabelAlignment(Qt.AlignRight)
        fmt_form.setSpacing(8)

        self._rate_combo = QComboBox()
        self._rate_combo.addItem("Device native (recommended)", userData=None)
        for r in SUPPORTED_SAMPLE_RATES:
            self._rate_combo.addItem(f"{r} Hz", userData=r)
        fmt_form.addRow("Sample rate:", self._rate_combo)

        self._block_combo = QComboBox()
        for b in SUPPORTED_BLOCK_SIZES:
            label = f"{b} frames"
            if b == 128:
                label += "  (proven stable)"
            elif b == 256:
                label += "  (default)"
            self._block_combo.addItem(label, userData=b)
        fmt_form.addRow("Buffer size:", self._block_combo)

        # ASIO toggle
        self._asio_check = QCheckBox("Use ASIO (requires ASIO-enabled PortAudio)")
        asio_ok = asio_available()
        self._asio_check.setEnabled(asio_ok)
        if not asio_ok:
            self._asio_check.setToolTip(
                "No ASIO host API found in this PortAudio build. "
                "Supply an ASIO-enabled PortAudio DLL to enable."
            )
        fmt_form.addRow("", self._asio_check)

        # Private Mode (WASAPI exclusive) toggle
        self._exclusive_check = QCheckBox("Enable Private Mode  (WASAPI Exclusive)")
        self._exclusive_check.setToolTip(
            "Captures your mic exclusively for MicHost — lower latency, "
            "no other app can listen in. Requires WASAPI devices on both sides."
        )
        self._exclusive_check.stateChanged.connect(self._on_exclusive_toggled)

        self._exclusive_warn = QLabel("⚠  Private Mode requires WASAPI devices on both input and output.")
        self._exclusive_warn.setProperty("class", "warn")
        self._exclusive_warn.setVisible(False)
        exclusive_col = QVBoxLayout()
        exclusive_col.setSpacing(2)
        exclusive_col.addWidget(self._exclusive_check)
        exclusive_col.addWidget(self._exclusive_warn)
        fmt_form.addRow("", exclusive_col)

        root.addWidget(fmt_group)

        # ── Remember / autostart ──────────────────────────────────────────────
        self._remember_check = QCheckBox(
            "Remember settings and autostart engine on next launch"
        )
        root.addWidget(self._remember_check)

        # ── Buttons ───────────────────────────────────────────────────────────
        self._add_separator(root)

        btns = QDialogButtonBox()
        self._apply_btn = btns.addButton("Apply", QDialogButtonBox.AcceptRole)
        btns.addButton("Cancel", QDialogButtonBox.RejectRole)
        btns.accepted.connect(self._on_apply)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _add_separator(self, layout: QVBoxLayout) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        layout.addWidget(line)

    # ── Populate dropdowns from live enumeration ──────────────────────────────

    def _populate(self) -> None:
        # Device combos
        self._in_combo.clear()
        self._out_combo.clear()

        for d in self._devices:
            label = d.qualified
            if d.is_vbcable:
                label += "  [VB-Cable]"
            if d.is_input:
                self._in_combo.addItem(label, userData=d)
            if d.is_output:
                self._out_combo.addItem(label, userData=d)

        # Restore saved selections
        saved_in  = self._current_settings.get("input_device") or ""
        saved_out = self._current_settings.get("output_device") or ""

        self._select_combo_by_name(self._in_combo,  saved_in,  self._suggestions.input)
        self._select_combo_by_name(self._out_combo, saved_out, self._suggestions.output)

        # Sample rate
        saved_rate = self._current_settings.get("samplerate")
        if saved_rate is None:
            self._rate_combo.setCurrentIndex(0)
        else:
            for i in range(self._rate_combo.count()):
                if self._rate_combo.itemData(i) == saved_rate:
                    self._rate_combo.setCurrentIndex(i)
                    break

        # Block size
        saved_block = self._current_settings.get("blocksize") or 256
        for i in range(self._block_combo.count()):
            if self._block_combo.itemData(i) == saved_block:
                self._block_combo.setCurrentIndex(i)
                break

        # ASIO
        self._asio_check.setChecked(bool(self._current_settings.get("asio", False)))

        # Private Mode — restore saved value, then gate on current device selection.
        # Connect combo signals here (after combos are populated) so that
        # _update_exclusive_gate fires whenever the user changes either device.
        # Use a flag to guard against double-connecting on refresh_devices() calls.
        # PySide6 emits a RuntimeWarning (not RuntimeError) on a failed disconnect,
        # so try/except is not reliable here — the flag is the correct pattern.
        if getattr(self, "_gate_signals_connected", False):
            self._in_combo.currentIndexChanged.disconnect(self._update_exclusive_gate)
            self._out_combo.currentIndexChanged.disconnect(self._update_exclusive_gate)
        self._in_combo.currentIndexChanged.connect(self._update_exclusive_gate)
        self._out_combo.currentIndexChanged.connect(self._update_exclusive_gate)
        self._gate_signals_connected = True

        self._exclusive_check.setChecked(
            bool(self._current_settings.get("exclusive_mode", False))
        )
        self._update_exclusive_gate()

        # Remember / autostart
        self._remember_check.setChecked(bool(self._current_settings.get("autostart", False)))

    def _select_combo_by_name(
        self, combo: QComboBox, saved_name: str, suggestion
    ) -> None:
        """Select saved device by name; fall back to suggestion; fall back to index 0."""
        if saved_name:
            for i in range(combo.count()):
                d = combo.itemData(i)
                if d and (d.qualified.lower() == saved_name.lower()
                          or d.name.lower() == saved_name.lower()):
                    combo.setCurrentIndex(i)
                    return
        if suggestion:
            for i in range(combo.count()):
                d = combo.itemData(i)
                if d and d.index == suggestion.index:
                    combo.setCurrentIndex(i)
                    return

    # ── Private Mode gating ───────────────────────────────────────────────────

    def _on_exclusive_toggled(self, state: int) -> None:
        """
        Show the explanation dialog when the user checks Private Mode.
        If they cancel, silently uncheck the box.
        No dialog shown when unchecking — that's always safe.
        """
        if state == Qt.Checked:
            confirmed = self._show_private_mode_dialog()
            if not confirmed:
                self._exclusive_check.blockSignals(True)
                self._exclusive_check.setChecked(False)
                self._exclusive_check.blockSignals(False)

    def _update_exclusive_gate(self) -> None:
        """
        Enable the Private Mode checkbox only when both selected devices are
        WASAPI.  If either is not WASAPI, disable and uncheck silently.
        Show/hide the inline warning accordingly.
        """
        in_entry  = self._in_combo.currentData()
        out_entry = self._out_combo.currentData()

        both_wasapi = (
            in_entry is not None and in_entry.is_wasapi and
            out_entry is not None and out_entry.is_wasapi
        )

        self._exclusive_check.setEnabled(both_wasapi)
        self._exclusive_warn.setVisible(not both_wasapi)

        if not both_wasapi:
            # Block signals so unchecking here doesn't trigger any stateChanged
            # handler the caller might add later.
            self._exclusive_check.blockSignals(True)
            self._exclusive_check.setChecked(False)
            self._exclusive_check.blockSignals(False)

    def _show_private_mode_dialog(self) -> bool:
        """
        Show the Private Mode explanation dialog.
        Returns True if the user confirmed, False if they cancelled.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("🔒 Private Mode — Exclusive Mic Capture")
        dlg.setMinimumWidth(480)
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Main explanation
        body = QLabel(
            "<b>MicHost will take direct, exclusive control of your microphone.</b>"
            "<br><br>"
            "✅ &nbsp;<b>Lower latency</b> — the signal goes straight to MicHost, "
            "no shared audio stack in the way.<br>"
            "✅ &nbsp;<b>Fully private</b> — no other app can intercept or listen "
            "to your raw mic while the engine is running."
            "<br><br>"
            "⚠️ &nbsp;<b>Your raw mic will be unavailable to other apps</b> while "
            "the engine is active. Video calls, browsers, and recording software "
            "won't be able to see it."
            "<br><br>"
            "<b>This is expected</b> — your processed audio is already on VB-Cable. "
            "Point other apps there instead of your mic."
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        layout.addWidget(body)

        # YouTube link
        link = QLabel(
            '📺 &nbsp;<a href="https://www.youtube.com/results?search_query='
            'change+microphone+input+discord+obs+vb+cable">'
            "Not sure how to do that? Watch a quick guide →</a>"
        )
        link.setTextFormat(Qt.RichText)
        link.setOpenExternalLinks(True)
        layout.addWidget(link)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        # Buttons
        btns = QDialogButtonBox()
        enable_btn = btns.addButton("Enable Private Mode", QDialogButtonBox.AcceptRole)
        btns.addButton("Cancel", QDialogButtonBox.RejectRole)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        result = dlg.exec()
        return result == QDialog.Accepted

    # ── Inline warnings ───────────────────────────────────────────────────────

    def _check_warnings(self) -> None:
        """Show/hide inline warning labels based on current state."""
        # VB-Cable
        has_vb = vbcable_present(self._devices)
        self._vbcable_warn.setVisible(not has_vb)

        # Device-not-found flags (only meaningful if devices were previously saved)
        val = validate_devices(self._current_settings, self._devices)

        if self._current_settings.get("input_device") and val.input_missing:
            name = self._current_settings['input_device']
            self._in_warning.setText(f"\u26a0  Device not found: \"{name}\"")
            self._in_warning.setVisible(True)
        else:
            self._in_warning.setVisible(False)

        if self._current_settings.get("output_device") and val.output_missing:
            name = self._current_settings['output_device']
            self._out_warning.setText(f"\u26a0  Device not found: \"{name}\"")
            self._out_warning.setVisible(True)
        else:
            self._out_warning.setVisible(False)

    def show_device_lost(self, name: str, kind: str) -> None:
        """Called externally when a device is lost at runtime."""
        if kind == "input":
            self._in_warning.setText(f"\u26a0  Device lost: \"{name}\"")
            self._in_warning.setVisible(True)
        else:
            self._out_warning.setText(f"\u26a0  Device lost: \"{name}\"")
            self._out_warning.setVisible(True)

    # ── Apply ─────────────────────────────────────────────────────────────────

    def _on_apply(self) -> None:
        in_entry  = self._in_combo.currentData()
        out_entry = self._out_combo.currentData()

        if in_entry is None or out_entry is None:
            QMessageBox.warning(self, "MicHost", "Please select both input and output devices.")
            return

        # VB-Cable gate — confirm if missing
        if not vbcable_present(self._devices):
            resp = QMessageBox.question(
                self, "No VB-Cable Found",
                "No VB-Cable device was found. Routing audio to a real output "
                "may cause feedback.\n\nStart anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return

        # Build new settings dict
        rate_data  = self._rate_combo.currentData()    # None = device native
        block_data = self._block_combo.currentData()
        autostart  = self._remember_check.isChecked()

        new_settings = dict(self._current_settings)
        new_settings["input_device"]  = in_entry.qualified
        new_settings["output_device"] = out_entry.qualified
        new_settings["samplerate"]    = rate_data       # None → device native
        new_settings["blocksize"]     = block_data
        new_settings["asio"]          = self._asio_check.isChecked()
        new_settings["exclusive_mode"] = self._exclusive_check.isChecked()
        new_settings["autostart"]     = autostart

        # Persist immediately (device selection is the slow/error path;
        # we want settings on disk before attempting engine.start()).
        save_settings(new_settings)
        log.info(
            "Settings applied — in: %s | out: %s | rate: %s | block: %d",
            in_entry.qualified, out_entry.qualified, rate_data, block_data,
        )

        self.settings_applied.emit(new_settings)
        self.accept()

    # ── Public: refresh after device change (e.g. USB hotplug) ───────────────

    def refresh_devices(self) -> None:
        self._devices = enumerate_devices()
        self._suggestions = suggest_devices(self._devices)
        self._populate()
        self._check_warnings()
