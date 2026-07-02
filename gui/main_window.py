"""
main_window.py — MicHost main window (Section 12.2).

Layout (top → bottom, mirroring signal flow):
  Management bar    — Settings button, restart indicator
  Preset header     — current preset name, dropdown, new/save-as/delete
  Input block       — device info readouts + dB gauge (pre-chain)
  VST chain block   — ordered plugin slots (add, move, bypass, remove, editor)
  Output block      — device info readouts + dB gauge (master)
  Footer            — Start / Stop / Mute  |  latency  |  xrun counter

Engine interaction:
  - Never calls engine methods directly from signal handlers; all calls are
    dispatched through _engine_call() which checks engine state first.
  - Structural mutations (add/remove/reorder) call _trigger_restart().
  - Live commands (mute, bypass) post via engine.set_mute / engine.set_bypass.
  - Meters polled by QTimer at ~30 ms.

Editor windows (Section 8.4):
  - One daemon thread per open editor.
  - EnumWindows snapshot-diff to capture HWND.
  - WM_CLOSE on restart / close.
"""

import logging
import os
import threading
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget, QFrame,
)

from engine import AudioEngine
from settings import (
    load_settings, save_settings, scan_vst3,
    find_device_by_name, enumerate_devices,
    SESSION_PATH, PRESETS_DIR,
)
from persistence import (
    capture_raw_state, save_session, load_session,
    save_preset, load_preset, list_presets, delete_preset,
    build_chain_objects,
)
from .styles import DbGauge, C_TEXT_WARN
from .settings_view import SettingsView

log = logging.getLogger(__name__)

# Optional: pywin32 for editor window management
try:
    import win32gui
    import win32con
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False
    log.warning("pywin32 not available — editor WM_CLOSE will not work.")


# ── Plugin slot widget ────────────────────────────────────────────────────────

class PluginSlot(QWidget):
    """
    One row in the chain block.
    Signals: move_up, move_down, bypass_toggled, remove, open_editor.
    """

    move_up        = Signal(int)    # slot index
    move_down      = Signal(int)    # slot index
    bypass_toggled = Signal(int, bool)  # slot index, new bypass state
    remove         = Signal(int)
    open_editor    = Signal(int)

    def __init__(self, index: int, name: str, bypassed: bool = False, parent=None):
        super().__init__(parent)
        self.index = index
        self._bypassed = bypassed

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        # Gauge
        self.gauge = DbGauge(self, width=6, height=36)
        layout.addWidget(self.gauge)

        # Plugin name
        self._name_label = QLabel(name)
        self._name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self._name_label)

        # Controls
        self._up_btn    = self._btn("▲", lambda: self.move_up.emit(self.index))
        self._down_btn  = self._btn("▼", lambda: self.move_down.emit(self.index))
        self._bypass_btn= QPushButton("BYP")
        self._bypass_btn.setCheckable(True)
        self._bypass_btn.setChecked(bypassed)
        self._bypass_btn.setFixedWidth(38)
        self._bypass_btn.clicked.connect(self._on_bypass)
        self._editor_btn= self._btn("UI",  lambda: self.open_editor.emit(self.index))
        self._remove_btn= self._btn("✕",  lambda: self.remove.emit(self.index))
        self._remove_btn.setProperty("class", "danger")

        for w in (self._up_btn, self._down_btn, self._bypass_btn,
                  self._editor_btn, self._remove_btn):
            layout.addWidget(w)

        self._update_bypass_style()
        self.setStyleSheet("PluginSlot { border-bottom: 1px solid #1e2025; }")

    def _btn(self, label: str, slot) -> QPushButton:
        b = QPushButton(label)
        b.setFixedWidth(32)
        b.clicked.connect(slot)
        return b

    def _on_bypass(self) -> None:
        self._bypassed = self._bypass_btn.isChecked()
        self._update_bypass_style()
        self.bypass_toggled.emit(self.index, self._bypassed)

    def _update_bypass_style(self) -> None:
        if self._bypassed:
            self._bypass_btn.setProperty("class", "bypassed")
            self._name_label.setStyleSheet("color: #6b7280; text-decoration: line-through;")
        else:
            self._bypass_btn.setProperty("class", "")
            self._name_label.setStyleSheet("")
        self._bypass_btn.style().unpolish(self._bypass_btn)
        self._bypass_btn.style().polish(self._bypass_btn)

    def set_interactive(self, enabled: bool) -> None:
        for w in (self._up_btn, self._down_btn, self._bypass_btn,
                  self._editor_btn, self._remove_btn):
            w.setEnabled(enabled)

    def update_gauge(self, db: float) -> None:
        self.gauge.update_level(db)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """
    The primary application window.

    Owns:
      - AudioEngine instance
      - Canonical chain description (list of dicts: path/name/bypassed/raw_state)
      - Editor registry {slot_index: (thread, hwnd)}
      - Preset list (loaded from ./presets/)
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MicHost")
        self.setMinimumSize(480, 680)

        self._engine = AudioEngine()
        self._settings = load_settings()
        self._chain_desc: list[dict] = []          # canonical chain description
        self._editor_registry: dict  = {}          # {slot_idx: {"hwnd":..., "done":Event}}
        self._shutdown_event         = threading.Event()   # signals worker threads to abort
        self._restarting             = False
        self._muted                  = False

        self._build_ui()
        self._setup_meter_timer()
        self._load_presets()
        self._refresh_device_labels()

        # Startup: validate devices, optionally autostart
        self._on_startup()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        root.addWidget(self._build_management_bar())
        root.addWidget(self._build_restart_banner())

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_content = QWidget()
        scroll_layout  = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(6)
        scroll_layout.setContentsMargins(8, 8, 8, 8)

        scroll_layout.addWidget(self._build_preset_bar())
        scroll_layout.addWidget(self._build_input_block())
        scroll_layout.addWidget(self._build_chain_block())
        scroll_layout.addWidget(self._build_output_block())
        scroll_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        root.addWidget(scroll_area, stretch=1)
        root.addWidget(self._build_footer())

    def _build_management_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet("background: #22252a; border-bottom: 1px solid #2e3138;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)

        title = QLabel("MICHOST")
        title.setStyleSheet("font-size: 11px; letter-spacing: 2px; color: #6b7280; font-weight: bold;")
        layout.addWidget(title)
        layout.addStretch()

        self._settings_btn = QPushButton("⚙  Settings")
        self._settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self._settings_btn)
        return bar

    def _build_restart_banner(self) -> QWidget:
        self._restart_banner = QWidget()
        self._restart_banner.setFixedHeight(28)
        self._restart_banner.setStyleSheet(
            f"background: #5a3e00; border-bottom: 1px solid #8a5c00;"
        )
        layout = QHBoxLayout(self._restart_banner)
        layout.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel("⟳  Restarting engine…")
        lbl.setStyleSheet("color: #e8a030; font-size: 11px;")
        layout.addWidget(lbl)
        self._restart_banner.setVisible(False)
        return self._restart_banner

    def _build_preset_bar(self) -> QGroupBox:
        box = QGroupBox("PRESET")
        layout = QHBoxLayout(box)
        layout.setSpacing(6)

        self._preset_combo = QComboBox()
        self._preset_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        layout.addWidget(self._preset_combo)

        for label, slot in (
            ("New",     self._new_preset),
            ("Save as", self._save_preset_as),
            ("Delete",  self._delete_preset),
        ):
            b = QPushButton(label)
            b.setFixedWidth(60)
            b.clicked.connect(slot)
            layout.addWidget(b)

        return box

    def _build_input_block(self) -> QGroupBox:
        box = QGroupBox("INPUT")
        layout = QHBoxLayout(box)
        layout.setSpacing(10)

        self._in_gauge = DbGauge(box, width=10, height=64)
        layout.addWidget(self._in_gauge)

        info = QVBoxLayout()
        self._in_device_lbl  = QLabel("—")
        self._in_format_lbl  = QLabel("—")
        self._in_device_lbl.setProperty("class", "mono")
        self._in_format_lbl.setProperty("class", "dim")
        info.addWidget(self._in_device_lbl)
        info.addWidget(self._in_format_lbl)
        info.addStretch()
        layout.addLayout(info, stretch=1)
        return box

    def _build_chain_block(self) -> QGroupBox:
        self._chain_box = QGroupBox("CHAIN")
        self._chain_layout = QVBoxLayout(self._chain_box)
        self._chain_layout.setSpacing(0)
        self._chain_layout.setContentsMargins(4, 4, 4, 4)

        self._empty_chain_lbl = QLabel("No plugins.  Add one below.")
        self._empty_chain_lbl.setProperty("class", "dim")
        self._empty_chain_lbl.setAlignment(Qt.AlignCenter)
        self._empty_chain_lbl.setContentsMargins(0, 12, 0, 12)
        self._chain_layout.addWidget(self._empty_chain_lbl)

        # Add button
        add_row = QHBoxLayout()
        add_row.addStretch()
        self._add_btn = QPushButton("+ Add plugin")
        self._add_btn.clicked.connect(self._on_add_plugin)
        add_row.addWidget(self._add_btn)
        self._chain_layout.addLayout(add_row)

        return self._chain_box

    def _build_output_block(self) -> QGroupBox:
        box = QGroupBox("OUTPUT")
        layout = QHBoxLayout(box)
        layout.setSpacing(10)

        self._out_gauge = DbGauge(box, width=10, height=64)
        layout.addWidget(self._out_gauge)

        info = QVBoxLayout()
        self._out_device_lbl = QLabel("—")
        self._out_format_lbl = QLabel("—")
        self._out_device_lbl.setProperty("class", "mono")
        self._out_format_lbl.setProperty("class", "dim")
        info.addWidget(self._out_device_lbl)
        info.addWidget(self._out_format_lbl)
        info.addStretch()
        layout.addLayout(info, stretch=1)
        return box

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setFixedHeight(44)
        footer.setStyleSheet("background: #22252a; border-top: 1px solid #2e3138;")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        self._start_btn = QPushButton("▶  Start")
        self._start_btn.setProperty("class", "start")
        self._start_btn.setFixedWidth(80)
        self._start_btn.clicked.connect(self._on_start)
        layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton("■  Stop")
        self._stop_btn.setProperty("class", "stop")
        self._stop_btn.setFixedWidth(80)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        layout.addWidget(self._stop_btn)

        self._mute_btn = QPushButton("Mute")
        self._mute_btn.setProperty("class", "mute")
        self._mute_btn.setFixedWidth(60)
        self._mute_btn.clicked.connect(self._on_mute_toggle)
        layout.addWidget(self._mute_btn)

        layout.addStretch()

        self._latency_lbl = QLabel("— ms")
        self._latency_lbl.setProperty("class", "readout")
        self._latency_lbl.setToolTip("Nominal latency (blocksize / samplerate × 1000)")
        layout.addWidget(self._latency_lbl)

        sep = QLabel("|")
        sep.setProperty("class", "dim")
        layout.addWidget(sep)

        self._xrun_lbl = QLabel("xruns: 0")
        self._xrun_lbl.setProperty("class", "dim")
        layout.addWidget(self._xrun_lbl)

        return footer

    # ── Startup ───────────────────────────────────────────────────────────────

    def _on_startup(self) -> None:
        from settings import validate_devices
        devices = enumerate_devices()
        result  = validate_devices(self._settings, devices)

        if not result.ok:
            log.info("Startup device validation failed — opening settings.")
            self._open_settings(force=True)
            return
        
        # TODO: REFACTOR INTO PRESET MANAGEMENT
        # Restore last session as active chain.
        # Session overrides the first preset on startup — it is "what was open last."
        session_chain = load_session(SESSION_PATH)

        if session_chain:
            log.info("Restoring last session (%d slot(s)).", len(session_chain))
            self._chain_desc = session_chain
        else:
            # Fall back to first preset
            first = next(iter(self._presets.values()))
            self._chain_desc = list(first.get("chain", []))


        if self._settings.get("autostart"):
            log.info("Autostart enabled — starting engine.")
            self._start_engine()

    # ── Settings ──────────────────────────────────────────────────────────────

    def _open_settings(self, force: bool = False, device_lost: bool = False) -> None:
        view = SettingsView(self, device_lost=device_lost)
        view.settings_applied.connect(self._on_settings_applied)
        if force:
            view.exec()
        else:
            view.exec()

    @Slot(dict)
    def _on_settings_applied(self, new_settings: dict) -> None:
        changed = (
            new_settings.get("input_device")  != self._settings.get("input_device") or
            new_settings.get("output_device") != self._settings.get("output_device") or
            new_settings.get("samplerate")    != self._settings.get("samplerate") or
            new_settings.get("blocksize")     != self._settings.get("blocksize")
        )
        self._settings = new_settings

        if self._engine.running and changed:
            log.info("Settings changed while engine running — triggering restart.")
            self._trigger_restart()
        elif not self._engine.running and self._settings.get("autostart"):
            self._start_engine()

    # ── Engine start / stop ───────────────────────────────────────────────────

    def _start_engine(self) -> None:
        srate = self._settings.get("samplerate")
        if srate is None:
            # Resolve device native rate
            try:
                devices = enumerate_devices()
                d = find_device_by_name(self._settings.get("input_device", ""), devices)
                srate = d.default_samplerate if d else 48000.0
            except Exception:
                srate = 48000.0

        blocksize = self._settings.get("blocksize") or 256
        devices   = enumerate_devices()

        in_entry  = find_device_by_name(self._settings.get("input_device", ""), devices)
        out_entry = find_device_by_name(self._settings.get("output_device", ""), devices)

        if in_entry is None or out_entry is None:
            QMessageBox.warning(
                self, "MicHost",
                "Configured audio device not found.\nPlease open Settings and re-select."
            )
            return

        def on_missing(name: str):
            QMessageBox.warning(self, "Plugin Not Found",
                f"Plugin '{name}' was not found in ./vst3 and has been skipped.")

        def on_load_error(name: str, e: Exception):
            QMessageBox.warning(self, "Plugin Load Failed",
                f"Could not load '{name}': {e}\n\nSlot will be skipped.")

        # Build chain on the main thread (Pedalboard requires it).
        chain = build_chain_objects(
            self._chain_desc,
            on_missing=on_missing,
            on_load_error=on_load_error,
            shutdown_flag=self._shutdown_event
        )

        self._engine.set_chain(chain)

        try:
            self._engine.start(
                input_device=in_entry.index,
                output_device=out_entry.index,
                samplerate=srate,
                blocksize=blocksize,
            )
        except Exception as e:
            log.error("Engine start failed: %s", e)
            QMessageBox.critical(
                self, "Engine Start Failed",
                f"Could not start the audio engine:\n\n{e}\n\n"
                "Open Settings to check device configuration."
            )
            return

        self._on_engine_started()

    def _on_engine_started(self) -> None:
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._latency_lbl.setText(f"{self._engine.stream_info.get('latency_ms', '—')} ms")
        self._refresh_device_labels()
        self._rebuild_chain_ui()

    def _stop_engine(self) -> None:
        """
        Stop the engine and capture raw_state post-stop.
        All stop paths (manual, restart, shutdown) go through here. 
        raw_state is read only after the audio thread is confirmed dead.
        """
        if not self._engine.running:
            return

        try:
            while True:
                self._engine._command_q.get_nowait()
        except Exception:
            pass

        # Close editors, drain command_q, stop audio thread
        self._close_all_editors()
        try:
            while True:
                self._engine._command_q.get_nowait()
        except Exception:
            pass
        self._engine.stop()

        # Step 5: read raw_state — NOW safe, audio thread is confirmed dead.
        #         This is the save-before-stop invariant
        #         raw_state is an opaque C++ blob; reading while the audio thread
        #         is alive risks a data race at the native level.
        capture_raw_state(self._chain_desc, self._engine._active_chain)

        # TODO: we should make autosave here
        # we only use session for restarts

        self._on_engine_stopped()

    def _on_engine_stopped(self) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._latency_lbl.setText("— ms")
        self._xrun_lbl.setText("xruns: 0")
        # Device labels stay populated with configured names — they are static
        # references from settings; blanking them while stopped is confusing.
        self._refresh_device_labels()

    def _refresh_device_labels(self) -> None:
        """Show configured device names from settings regardless of engine state."""
        in_name  = self._settings.get("input_device")  or "—"
        out_name = self._settings.get("output_device") or "—"

        if self._engine.running:
            info = self._engine.stream_info
            self._in_device_lbl.setText(info.get("input_device", in_name))
            self._in_format_lbl.setText(
                f"{info.get('samplerate', 0):.0f} Hz  •  "
                f"{info.get('in_channels', 0)}ch  •  block {info.get('blocksize', 0)}"
            )
            self._out_device_lbl.setText(info.get("output_device", out_name))
            self._out_format_lbl.setText(f"{info.get('samplerate', 0):.0f} Hz  •  2ch")
        else:
            # Engine stopped — show configured names, dim format line
            self._in_device_lbl.setText(in_name)
            self._in_format_lbl.setText("stopped")
            self._out_device_lbl.setText(out_name)
            self._out_format_lbl.setText("stopped")

    @Slot()
    def _on_start(self) -> None:
        self._start_engine()

    @Slot()
    def _on_stop(self) -> None:
        self._stop_engine()

    @Slot()
    def _on_mute_toggle(self) -> None:
        self._muted = not self._muted
        self._engine.set_mute(self._muted)
        if self._muted:
            self._mute_btn.setText("Unmute")
            self._mute_btn.setProperty("class", "muted")
            # Immediately floor output gauge — don't wait for next poll tick
            self._out_gauge.update_level(self._out_gauge.FLOOR_DB)
        else:
            self._mute_btn.setText("Mute")
            self._mute_btn.setProperty("class", "mute")
        self._mute_btn.style().unpolish(self._mute_btn)
        self._mute_btn.style().polish(self._mute_btn)

    # ── Chain rebuild / restart sequence (Section 4.6, 9-step mandatory order) ──

    def _trigger_restart(self, new_settings: dict = None, new_chain: dict = None, new_preset: dict = None) -> None:
        """
        Universal restart — the single path for all structural changes and
        reconfiguration. Follows the mandatory 9-step order.

        new_settings: if provided, applies before restarting (reconfigure path).
        """

        if self._restarting:
            return
        
        self._restarting = True
        self._set_ui_interactive(False)
        self._restart_banner.setVisible(True)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        try:
            self._stop_engine()

            if new_settings:
                self._settings = new_settings
                save_settings(new_settings)

            if new_chain is not None:
                self._chain_desc = new_chain
            
            if new_preset:
                self._save_preset(new_preset)

            save_session(self._chain_desc, SESSION_PATH)

            # Pedalboard's load_plugin() has the same main-thread requirement as
            # show_editor() — it must not be called from a worker thread.
            # The worker-thread pattern is abandoned for this reason.

            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()   # let banner render before blocking

            self._start_engine()
            
        # UI re-enabled in finally block
        finally:
            if not self._shutdown_event.is_set():
                self._restarting = False
                self._restart_banner.setVisible(False)
                self._set_ui_interactive(True)

    def _set_ui_interactive(self, enabled: bool) -> None:
        self._settings_btn.setEnabled(enabled)
        self._add_btn.setEnabled(enabled)
        self._start_btn.setEnabled(enabled and not self._engine.running)
        self._stop_btn.setEnabled(enabled and self._engine.running)
        for slot in self._slot_widgets():
            slot.set_interactive(enabled)

    def _post_warning(self, message: str) -> None:
        """Thread-safe warning popup via QTimer (called from worker thread)."""
        QTimer.singleShot(0, lambda: QMessageBox.warning(self, "MicHost", message))

    # ── Chain description management ──────────────────────────────────────────

    # ── Chain UI ──────────────────────────────────────────────────────────────

    def _rebuild_chain_ui(self) -> None:
        """Rebuild the slot widgets from _chain_desc."""
        # Remove old slots (not the empty label or add button)
        for slot in self._slot_widgets():
            self._chain_layout.removeWidget(slot)
            slot.deleteLater()

        self._empty_chain_lbl.setVisible(len(self._chain_desc) == 0)

        for i, item in enumerate(self._chain_desc):
            slot = PluginSlot(i, item.get("name", "?"), item.get("bypassed", False))
            slot.move_up.connect(self._on_move_up)
            slot.move_down.connect(self._on_move_down)
            slot.bypass_toggled.connect(self._on_bypass_toggled)
            slot.remove.connect(self._on_remove_plugin)
            slot.open_editor.connect(self._on_open_editor)
            # Insert before the add button (last item in layout)
            self._chain_layout.insertWidget(self._chain_layout.count() - 1, slot)

    def _slot_widgets(self) -> list:
        result = []
        for i in range(self._chain_layout.count()):
            item = self._chain_layout.itemAt(i)
            if item and isinstance(item.widget(), PluginSlot):
                result.append(item.widget())
        return result

    @Slot()
    def _on_add_plugin(self) -> None:
        paths = scan_vst3()
        if not paths:
            QMessageBox.information(self, "MicHost", "No plugins found in ./vst3.")
            return

        names = [os.path.splitext(os.path.basename(p))[0] for p in paths]
        # Filter already-loaded paths
        loaded = {d["path"] for d in self._chain_desc}
        available = [(n, p) for n, p in zip(names, paths)]  # allow duplicates

        choice, ok = QInputDialog.getItem(
            self, "Add Plugin", "Select plugin:", [n for n, _ in available],
            editable=False
        )
        if not ok or not choice:
            return

        idx = [n for n, _ in available].index(choice)
        path = available[idx][1]
        self._chain_desc.append({
            "path": path,
            "name": os.path.splitext(os.path.basename(path))[0],
            "bypassed": False,
            "raw_state": None,
        })
        self._rebuild_chain_ui()
        self._trigger_restart()

    @Slot(int)
    def _on_move_up(self, index: int) -> None:
        if index <= 0:
            return
        self._chain_desc.insert(index - 1, self._chain_desc.pop(index))
        self._rebuild_chain_ui()
        self._trigger_restart()

    @Slot(int)
    def _on_move_down(self, index: int) -> None:
        if index >= len(self._chain_desc) - 1:
            return
        self._chain_desc.insert(index + 1, self._chain_desc.pop(index))
        self._rebuild_chain_ui()
        self._trigger_restart()

    @Slot(int, bool)
    def _on_bypass_toggled(self, index: int, bypassed: bool) -> None:
        if 0 <= index < len(self._chain_desc):
            self._chain_desc[index]["bypassed"] = bypassed
        # Live command — no restart needed
        self._engine.set_bypass(index, bypassed)

    @Slot(int)
    def _on_remove_plugin(self, index: int) -> None:
        if 0 <= index < len(self._chain_desc):
            self._chain_desc.pop(index)
            self._rebuild_chain_ui()
            self._trigger_restart()

    # ── Plugin editor windows (Section 8.4) ───────────────────────────────────
    #
    # Constraint: Pedalboard's show_editor() MUST be called from the main
    # (Qt GUI) thread. It is also blocking — it pumps the OS message loop
    # internally until the user closes the window.
    #
    # Pattern:
    #   1. Snapshot existing HWNDs on the main thread (before the call).
    #   2. Post show_editor() to the main thread via QTimer.singleShot(0).
    #      This returns immediately to the caller; show_editor() runs at the
    #      next event-loop iteration, blocking there until the window closes.
    #   3. Start a background thread to poll for the new HWND via EnumWindows
    #      snapshot-diff. Store it in the registry when found.
    #   4. The registry entry tracks (hwnd_holder, alive_flag) instead of a
    #      thread — the editor is not "on a thread" anymore; it's on the main
    #      event loop. alive_flag is a threading.Event cleared when
    #      show_editor() returns.

    @Slot(int)
    def _on_open_editor(self, index: int) -> None:
        if not _WIN32_AVAILABLE:
            QMessageBox.information(
                self, "Editor",
                "pywin32 is not installed — plugin editor windows require it.\n"
                "Install with: pip install pywin32"
            )
            return

        # Prevent duplicate open editors for this slot
        existing = self._editor_registry.get(index)
        if existing and not existing["done"].is_set():
            log.info("Editor for slot %d already open.", index)
            return

        chain = self._engine._active_chain
        if index >= len(chain):
            return
        board = chain[index][0]
        plugins = list(board)
        if not plugins:
            return
        plugin = plugins[0]

        # 1. Snapshot before — must happen before show_editor() is posted
        handles_before: set = set()
        win32gui.EnumWindows(lambda h, _: handles_before.add(h), None)

        # Registry entry: hwnd=None until capture thread finds it; done set when closed
        done_event = threading.Event()
        entry = {"hwnd": None, "done": done_event}
        self._editor_registry[index] = entry

        # 2. Post show_editor() to the main thread event loop.
        #    QTimer.singleShot(0) fires at the next idle slot — on the main thread.
        #    It blocks here until the plugin window is closed by the user.
        def call_show_editor():
            try:
                plugin.show_editor()
                log.info("Editor for slot %d closed by user.", index)
            except Exception as e:
                log.error("show_editor for slot %d raised: %s", index, e)
            finally:
                done_event.set()
                # Clear hwnd so _close_all_editors knows it's already gone
                if index in self._editor_registry:
                    self._editor_registry[index]["hwnd"] = None

        QTimer.singleShot(0, call_show_editor)

        # 3. Background thread: poll EnumWindows for the new HWND
        def capture_hwnd():
            import time
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                time.sleep(0.010)
                if done_event.is_set():
                    # show_editor() already returned (very fast open+close) — no hwnd needed
                    return
                current: set = set()
                win32gui.EnumWindows(lambda h, _: current.add(h), None)
                new = current - handles_before
                if new:
                    hwnd = next(iter(new))
                    log.info("Captured editor HWND %d for slot %d.", hwnd, index)
                    if index in self._editor_registry:
                        self._editor_registry[index]["hwnd"] = hwnd

                    # ── Reposition the window to a usable location ──────────────
                    self._move_editor_window(hwnd)
                    return

            log.warning(
                "Could not capture editor HWND for slot %d (timeout). "
                "WM_CLOSE will not be available for this editor.", index
            )

        hwnd_thread = threading.Thread(target=capture_hwnd, daemon=True, name=f"hwnd-cap-{index}")
        hwnd_thread.start()

    def _move_editor_window(self, hwnd: int, x: int = 200, y: int = 200) -> None:
        try:
            hwnd = int(hwnd)  # ensure it's a plain int, not some wrapper object

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width  = right  - left
            height = bottom - top

            SWP_NOSIZE   = 0x0001
            SWP_NOZORDER = 0x0004

            win32gui.SetWindowPos(hwnd, 0, x, y, width, height, SWP_NOSIZE | SWP_NOZORDER)
            log.info("Moved editor HWND %d to (%d, %d).", hwnd, x, y)

        except Exception as e:
            log.warning("Failed to move editor window: %s", e) 
            
    def _close_all_editors(self) -> None:
        """
        Post WM_CLOSE to all open editor windows and wait for show_editor()
        to return (Section 8.4).

        show_editor() runs on the main thread, so we cannot join() it the
        normal way. Instead we wait on each entry's done_event, which is set
        when show_editor() returns after the window receives WM_CLOSE.
        """
        if not _WIN32_AVAILABLE:
            return

        for slot, entry in list(self._editor_registry.items()):
            hwnd = entry.get("hwnd")
            done = entry.get("done")

            if hwnd is not None:
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    log.debug("Posted WM_CLOSE to HWND %d (slot %d).", hwnd, slot)
                except Exception as e:
                    log.warning("WM_CLOSE failed for slot %d: %s", slot, e)

            # Wait for show_editor() to return; timeout is a safety net.
            # If done is None (shouldn't happen) or times out, proceed anyway —
            # daemon thread semantics mean the editor dies with the process.
            if done is not None and not done.is_set():
                # We're on the main thread and show_editor() is ALSO on the main
                # thread, so waiting here would deadlock. Instead: pump events
                # briefly to let show_editor() process the WM_CLOSE message.
                import time
                from PySide6.QtWidgets import QApplication
                deadline = time.monotonic() + 2.0
                while not done.is_set() and time.monotonic() < deadline:
                    QApplication.processEvents()
                    time.sleep(0.020)
                if not done.is_set():
                    log.warning("Editor for slot %d did not close within timeout.", slot)

        self._editor_registry.clear()

    # ── Presets ───────────────────────────────────────────────────────────────

    def _load_presets(self) -> None:
        """
        Populate preset combo from ./presets/ using persistence layer.
        Restores the last session (session.json) as the active chain.
        If no presets exist, creates a 'Default' preset.
        """
        presets_list = list_presets(PRESETS_DIR)

        self._presets: dict[str, dict] = {}
        for data in presets_list:
            name = data.get("name", "Unnamed")
            self._presets[name] = data

        if not self._presets:
            default_data = {"version": 1, "name": "Default", "chain": []}
            self._presets["Default"] = default_data
            save_preset("Default", [], PRESETS_DIR)

        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for name in self._presets:
            self._preset_combo.addItem(name)
        self._preset_combo.blockSignals(False)

        self._rebuild_chain_ui()

    @Slot(int)
    def _on_preset_selected(self, index: int) -> None:
        if index < 0:
            return
        name = self._preset_combo.itemText(index)
        data = self._presets.get(name)
        if not data:
            return

        if self._engine.running:
            resp = QMessageBox.question(
                self, "Load Preset",
                f"Load preset '{name}'? This will briefly stop the engine.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                # Revert combo without triggering signal
                self._preset_combo.blockSignals(True)
                self._preset_combo.setCurrentText(self._current_preset_name())
                self._preset_combo.blockSignals(False)
                return

        self._apply_preset_by_name(name)
        
    
    def _apply_preset_by_name(self, name: str) -> None:
        log.info("Applying preset: " + os.path.join(PRESETS_DIR, f"{name}.json"))
        new_chain = load_preset(os.path.join(PRESETS_DIR, f"{name}.json")).get("chain", [])

        self._trigger_restart(new_chain=new_chain)

    def _current_preset_name(self) -> str:
        return self._preset_combo.currentText()

    @Slot()
    def _new_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "New Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        data = {"version": 1, "name": name, "chain": []}

        self._preset_combo.blockSignals(True)
        self._presets[name] = data
        self._preset_combo.addItem(name)
        self._preset_combo.setCurrentText(name)
        self._preset_combo.blockSignals(False)
        
        self._trigger_restart(new_chain=[], new_preset=data)

    @Slot()
    def _save_preset_as(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save Preset As", "Preset name:",
            text=self._current_preset_name()
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        data = {"version": 1, "name": name, "chain": list(self._chain_desc)}
        self._presets[name] = data
        if self._preset_combo.findText(name) < 0:
            self._preset_combo.addItem(name)
        self._preset_combo.setCurrentText(name)
        self._trigger_restart(new_preset=data)

    @Slot()
    def _save_preset(self, data : dict) -> None:
        save_preset(data["name"], data["chain"], PRESETS_DIR)

    @Slot()
    def _delete_preset(self) -> None:
        name = self._current_preset_name()
        if len(self._presets) <= 1:
            QMessageBox.information(self, "MicHost", "Cannot delete the last preset.")
            return
        resp = QMessageBox.question(
            self, "Delete Preset", f"Delete preset '{name}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        self._presets.pop(name, None)
        import re
        safe = re.sub(r"[^\w\- ]", "_", name)
        delete_preset(os.path.join(PRESETS_DIR, f"{safe}.json"))
        idx = self._preset_combo.findText(name)
        if idx >= 0:
            self._preset_combo.removeItem(idx)

        if not self._presets:
            default_data = {"version": 1, "name": "Default", "chain": []}
            self._presets["Default"] = default_data
            save_preset("Default", [], PRESETS_DIR)
            self._preset_combo.addItem("Default")
            self._preset_combo.setCurrentText("Default")

    # ── Meter polling ─────────────────────────────────────────────────────────

    def _setup_meter_timer(self) -> None:
        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(33)   # ~30 Hz
        self._meter_timer.timeout.connect(self._poll_meters)
        self._meter_timer.start()

    @Slot()
    def _poll_meters(self) -> None:
        if not self._engine.meter_q:
            return
        payload = self._engine.meter_q[-1]

        self._in_gauge.update_level(payload["input"]["peak"])

        # Output gauge: suppress when muted — the audio is silenced at the
        # engine level; showing residual readings would confuse users.
        if self._muted:
            self._out_gauge.update_level(self._out_gauge.FLOOR_DB)
        else:
            self._out_gauge.update_level(payload["master"]["peak"])

        # Plugin gauges: suppress when bypassed — the plugin is not in signal
        # path; showing its readings would imply it is active.
        slots = self._slot_widgets()
        plugin_meters = payload.get("plugins", [])
        for i, slot in enumerate(slots):
            if i < len(plugin_meters):
                if slot._bypassed:
                    slot.update_gauge(slot.gauge.FLOOR_DB)
                else:
                    slot.update_gauge(plugin_meters[i]["peak"])

        self._xrun_lbl.setText(f"xruns: {self._engine.xrun_count}")

    # ── Close event (graceful shutdown, Section 11) ─────────────────────────
    #
    # Mandatory order. Do not reorder (Section 11).

    def closeEvent(self, event) -> None:
        from PySide6.QtWidgets import QApplication

        log.info("Shutdown initiated.")

        # Signal intent, disable UI, stop meter timer
        self._set_ui_interactive(False)
        self._meter_timer.stop()
        QApplication.processEvents()

        # Signal worker threads to abort
        self._shutdown_event.set()
        self._stop_engine()

        # TODO: refactor?
        # Save session + settings to disk
        save_session(self._chain_desc, SESSION_PATH)
        save_settings(self._settings)

        log.info("Shutdown complete.")
        event.accept()