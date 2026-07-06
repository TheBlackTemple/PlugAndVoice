"""
settings_view.py — Settings / entry-point dialog.

Responsibilities (Section 12.1):
  - Device dropdowns filtered to WASAPI by default; "Show all audio APIs"
    power toggle reveals the full enumeration.
  - Ranked candidates (up to 3 per role) with ★ mark on the top pick.
  - Live pair-validation status line: green / amber / red, blocks Apply on BLOCK.
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
from PySide6.QtGui import QDesktopServices, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QHBoxLayout, QFrame,
)

from settings import (
    load_settings, save_settings,
    enumerate_devices, suggest_devices,
    rank_input_candidates, rank_output_candidates,
    find_device_by_name, validate_devices, validate_pair,
    vbcable_present, asio_available,
    PairSeverity,
    SUPPORTED_SAMPLE_RATES, SUPPORTED_BLOCK_SIZES,
)
from .styles import C_TEXT_WARN, C_TEXT_ERR

log = logging.getLogger(__name__)

# Status indicator characters — plain unicode, no emoji needed.
_INDICATOR_OK   = "●"   # filled circle; coloured green via stylesheet
_INDICATOR_WARN = "●"   # same glyph, coloured amber
_INDICATOR_ERR  = "●"   # same glyph, coloured red
_STAR            = "★ "  # prepended to top-ranked combo item label

# Stylesheet colour tokens for the pair status line.
_C_OK   = "#4caf50"
_C_WARN = C_TEXT_WARN   # from styles module — keep consistent
_C_ERR  = C_TEXT_ERR


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
        self._show_all_apis = False        # power toggle state
        self._gate_signals_connected = False

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

        # Input row
        self._in_combo = QComboBox()
        self._in_warning = QLabel()
        self._in_warning.setProperty("class", "err")
        self._in_warning.setVisible(False)
        in_col = QVBoxLayout()
        in_col.setSpacing(2)
        in_col.addWidget(self._in_combo)
        in_col.addWidget(self._in_warning)
        dev_form.addRow("Input:", in_col)

        # Output row
        self._out_combo = QComboBox()
        self._out_warning = QLabel()
        self._out_warning.setProperty("class", "err")
        self._out_warning.setVisible(False)
        out_col = QVBoxLayout()
        out_col.setSpacing(2)
        out_col.addWidget(self._out_combo)
        out_col.addWidget(self._out_warning)
        dev_form.addRow("Output:", out_col)

        # Pair status line — sits between the two combos visually via the form,
        # but logically belongs to both.  Single line; colour carries severity.
        self._pair_status = QLabel()
        self._pair_status.setVisible(False)
        dev_form.addRow("", self._pair_status)

        # Power toggle — right-aligned, small, unobtrusive.
        self._all_apis_check = QCheckBox("Show all audio APIs")
        self._all_apis_check.setToolTip(
            "Off: shows only WASAPI devices (recommended).\n"
            "On: shows every host API — use if your device doesn't appear."
        )
        self._all_apis_check.stateChanged.connect(self._on_all_apis_toggled)
        dev_form.addRow("", self._all_apis_check)

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
            "Captures your mic exclusively for MicHost, "
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
        """
        Fill both device combos from ranked candidates.

        When _show_all_apis is False (default): WASAPI entries only, up to 3
        per role.  The top-ranked item gets a ★ prefix so the user can see at
        a glance which one we recommend.

        When _show_all_apis is True: full enumeration, same ★ logic.

        Saved settings are restored first; suggestion is the fallback for a
        first-run or unknown device.  Index 0 is the last resort.
        """
        # Disconnect pair-change signals before clearing to avoid spurious
        # validation calls mid-repopulate.
        self._disconnect_pair_signals()

        self._in_combo.clear()
        self._out_combo.clear()

        in_candidates  = rank_input_candidates(self._devices,  all_apis=self._show_all_apis)
        out_candidates = rank_output_candidates(self._devices, all_apis=self._show_all_apis)

        # Track the top picks so we can mark them with ★.
        top_in_idx  = in_candidates[0].index  if in_candidates  else None
        top_out_idx = out_candidates[0].index if out_candidates else None

        for d in in_candidates:
            label = self._device_label(d, top_in_idx)
            self._in_combo.addItem(label, userData=d)

        for d in out_candidates:
            label = self._device_label(d, top_out_idx)
            self._out_combo.addItem(label, userData=d)

        # Restore saved selection → fall back to suggestion → fall back to 0.
        saved_in  = self._current_settings.get("input_device")  or ""
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

        # Private Mode — restore, then gate on current selection.
        self._exclusive_check.blockSignals(True)
        self._exclusive_check.setChecked(
            bool(self._current_settings.get("exclusive_mode", False))
        )
        self._exclusive_check.blockSignals(False)

        # Remember / autostart
        self._remember_check.setChecked(bool(self._current_settings.get("autostart", False)))

        # Re-connect and run initial validation.
        self._connect_pair_signals()
        self._on_pair_changed()

    def _device_label(self, d: "DeviceEntry", top_idx: int | None) -> str:
        """
        Build the display label for a combo item.

        ★ prefix on the top-ranked entry.
        [VB-Cable] suffix on VB-Cable devices.
        Host API is already in d.qualified; no extra annotation needed in
        the default (WASAPI-only) view — they're all the same API.
        In all_apis mode the host API suffix in d.qualified is enough context.
        """
        star  = _STAR if (top_idx is not None and d.index == top_idx) else "  "
        label = f"{star}{d.qualified}"
        if d.is_vbcable:
            label += "  [VB-Cable]"
        return label

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
        # index 0 is implicit — QComboBox default

    # ── Signal wiring helpers ─────────────────────────────────────────────────

    def _connect_pair_signals(self) -> None:
        if not self._gate_signals_connected:
            self._in_combo.currentIndexChanged.connect(self._on_pair_changed)
            self._out_combo.currentIndexChanged.connect(self._on_pair_changed)
            self._gate_signals_connected = True

    def _disconnect_pair_signals(self) -> None:
        if self._gate_signals_connected:
            self._in_combo.currentIndexChanged.disconnect(self._on_pair_changed)
            self._out_combo.currentIndexChanged.disconnect(self._on_pair_changed)
            self._gate_signals_connected = False

    # ── Live pair validation (fires on every combo change) ────────────────────

    def _on_pair_changed(self) -> None:
        """
        Validate the current input+output pair and update the status line.

        Also gates the Apply button and the Private Mode checkbox, so all
        three concerns stay in sync from a single signal handler.
        """
        in_entry  = self._in_combo.currentData()
        out_entry = self._out_combo.currentData()

        result = validate_pair(in_entry, out_entry)
        self._update_pair_status(result)
        self._update_exclusive_gate(in_entry, out_entry)

    def _update_pair_status(self, result: "PairValidation") -> None:
        """Render the status indicator line and gate the Apply button."""
        if result.severity == PairSeverity.OK:
            colour    = _C_OK
            indicator = _INDICATOR_OK
        elif result.severity == PairSeverity.WARN:
            colour    = _C_WARN
            indicator = _INDICATOR_WARN
        else:  # BLOCK
            colour    = _C_ERR
            indicator = _INDICATOR_ERR

        self._pair_status.setText(
            f'<span style="color:{colour}; font-size:14px;">{indicator}</span>'
            f'&nbsp; {result.message}'
        )
        self._pair_status.setTextFormat(Qt.RichText)
        self._pair_status.setVisible(True)

        # Only block Apply on BLOCK severity; warn still allows applying
        # (user gets a confirm dialog in _on_apply instead).
        self._apply_btn.setEnabled(not result.block)

    # ── Private Mode gating ───────────────────────────────────────────────────

    def _update_exclusive_gate(
        self,
        in_entry:  "DeviceEntry | None" = None,
        out_entry: "DeviceEntry | None" = None,
    ) -> None:
        if in_entry is None:
            in_entry  = self._in_combo.currentData()
        if out_entry is None:
            out_entry = self._out_combo.currentData()

        both_wasapi = (
            in_entry  is not None and in_entry.is_wasapi and
            out_entry is not None and out_entry.is_wasapi
        )

        self._exclusive_check.setEnabled(both_wasapi)
        self._exclusive_warn.setVisible(not both_wasapi)

        if not both_wasapi and self._exclusive_check.isChecked():
            self._exclusive_check.blockSignals(True)
            self._exclusive_check.setChecked(False)
            self._exclusive_check.blockSignals(False)

    def _on_exclusive_toggled(self, state: int) -> None:
        """
        Show the explanation dialog when the user checks Private Mode.
        If they cancel, silently uncheck the box.
        No dialog shown when unchecking — that's always safe.
        """
        if self._exclusive_check.isChecked():
            confirmed = self._show_private_mode_dialog()
            if not confirmed:
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

        body = QLabel(
            "<b>MicHost will take direct, exclusive control of your microphone.</b>"
            "<br><br>"
            "✅ &nbsp;<b>Fully private</b> — no other app can intercept or listen "
            "to your raw mic while the engine is running."
            "<br><br>"
            "⚠️ &nbsp;<b>Latency</b> — the signal goes straight to MicHost, "
            "<b>latency times can improve or worsen depending on your microphone drivers.</b>"
            "<br><br>"
            "⚠️ &nbsp;<b>Your raw mic will be unavailable to other apps</b> while "
            "the engine is active. Video calls, browsers, and recording software "
            "won't be able to see it."
            "<br><br>"
            "⚠️ &nbsp;For best results, disable power management on your audio devices in Device Manager."
            "<br><br>"
            "Remember, <b>your new output device is VB-Cable Output</b> or whichever "
            "you chose in settings. Point other apps there instead of your mic."
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        layout.addWidget(body)

        link = QLabel(
            'Not sure how to do that? '
            '📺 &nbsp;<a href="https://www.youtube.com/results?search_query='
            'change+microphone+input+discord+obs+vb+cable">'
            "Watch a quick guide →</a>"
        )
        link.setTextFormat(Qt.RichText)
        link.setOpenExternalLinks(True)
        layout.addWidget(link)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        btns = QDialogButtonBox()
        btns.addButton("Enable Private Mode", QDialogButtonBox.AcceptRole)
        btns.addButton("Cancel", QDialogButtonBox.RejectRole)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        return dlg.exec() == QDialog.Accepted

    # ── Power toggle ──────────────────────────────────────────────────────────

    def _on_all_apis_toggled(self, state: int) -> None:
        """
        Repopulate combos with the full device list when the power toggle is on.
        The current selection is preserved if the device still appears; otherwise
        it falls back to the saved name → suggestion → index 0.
        """
        self._show_all_apis = bool(state)
        self._populate()
        self._check_warnings()

    # ── Inline warnings ───────────────────────────────────────────────────────

    def _check_warnings(self) -> None:
        """Show/hide inline warning labels based on current state."""
        # VB-Cable
        has_vb = vbcable_present(self._devices)
        self._vbcable_warn.setVisible(not has_vb)

        # Device-not-found flags (only meaningful if devices were previously saved)
        val = validate_devices(self._current_settings, self._devices)

        if self._current_settings.get("input_device") and val.input_missing:
            name = self._current_settings["input_device"]
            self._in_warning.setText(f"\u26a0  Device not found: \"{name}\"")
            self._in_warning.setVisible(True)
        else:
            self._in_warning.setVisible(False)

        if self._current_settings.get("output_device") and val.output_missing:
            name = self._current_settings["output_device"]
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

        # Re-validate at apply time (defensive; Apply should already be disabled
        # on BLOCK severity, but guard against edge cases).
        pair = validate_pair(in_entry, out_entry)
        if pair.block:
            QMessageBox.critical(
                self, "Cannot start",
                f"Device conflict: {pair.message}\n\nChange your selection and try again.",
            )
            return

        # Warn-severity path: one confirm, then proceed.
        if pair.warn:
            resp = QMessageBox.question(
                self, "Check your devices",
                f"{pair.message}\n\nStart anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return

        # Build new settings dict
        rate_data  = self._rate_combo.currentData()
        block_data = self._block_combo.currentData()
        autostart  = self._remember_check.isChecked()

        new_settings = dict(self._current_settings)
        new_settings["input_device"]   = in_entry.qualified
        new_settings["output_device"]  = out_entry.qualified
        new_settings["samplerate"]     = rate_data
        new_settings["blocksize"]      = block_data
        new_settings["asio"]           = self._asio_check.isChecked()
        new_settings["exclusive_mode"] = self._exclusive_check.isChecked()
        new_settings["autostart"]      = autostart

        save_settings(new_settings)
        log.info(
            "Settings applied — in: %s | out: %s | rate: %s | block: %d",
            in_entry.qualified, out_entry.qualified, rate_data, block_data,
        )

        self.settings_applied.emit(new_settings)
        self.accept()

    # ── Public: refresh after device change (e.g. USB hotplug) ───────────────

    def refresh_devices(self) -> None:
        self._devices     = enumerate_devices()
        self._suggestions = suggest_devices(self._devices)
        self._populate()
        self._check_warnings()
