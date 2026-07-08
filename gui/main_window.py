"""
main_window.py — MicHost main window (Section 12.2).

Layout (top → bottom, mirroring signal flow):
  Management bar    — Settings button, restart indicator
  Preset header     — current preset name, dropdown, new/save-as/delete
  Input block       — device info readouts + dB gauge (pre-chain) + driver latency reported
  VST chain block   — ordered plugin slots (add, move, bypass, remove, editor)
  Output block      — device info readouts + dB gauge (master)
  Footer            — Start / Stop / Mute  | xrun counter

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
import time
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
    write_autosave, list_autosaves, load_autosave,
)
from .styles import DbGauge, C_TEXT_WARN
from .settings_view import SettingsView

log = logging.getLogger(__name__)

# Heartbeat watchdog threshold.
# At 48kHz/256 frames the callback fires every ~5.3ms.
# 500ms is ~94 missed callbacks — far beyond any legitimate scheduling jitter.
_CALLBACK_STALL_TIMEOUT = 0.5

# Maximum consecutive watchdog-triggered restart attempts before giving up.
# Prevents an infinite restart loop when the device is persistently unavailable.
_MAX_RESTART_ATTEMPTS = 3

# Hotkey IDs: unique integers passed to RegisterHotKey / WM_HOTKEY wParam.
# Preset bindings use IDs starting at _HK_PRESET_BASE.
_HK_ID_MUTE         = 1
_HK_ID_START        = 2
_HK_ID_STOP         = 3
_HK_PRESET_BASE     = 100   # preset IDs: 100, 101, 102, …

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
        self._restart_attempts       = 0     # consecutive watchdog-triggered restart attempts

        self._build_ui()
        self._setup_meter_timer()
        self._load_presets()
        self._refresh_device_labels()
        
        self._hotkey_id_map: dict[int, str] = {}   # hk_id → action tag
        self._install_hotkeys()

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

        self._autosaves_btn = QPushButton("🕓  Autosaves")
        self._autosaves_btn.clicked.connect(self._open_autosaves)
        layout.addWidget(self._autosaves_btn)

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
        self._in_latency_lbl = QLabel("—")
        self._in_device_lbl.setProperty("class", "mono")
        self._in_format_lbl.setProperty("class", "dim")
        self._in_latency_lbl.setProperty("class", "dim")
        info.addWidget(self._in_device_lbl)
        info.addWidget(self._in_format_lbl)
        info.addWidget(self._in_latency_lbl)
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
        self._mute_btn.setFixedWidth(75)
        self._mute_btn.clicked.connect(self._on_mute_toggle)
        layout.addWidget(self._mute_btn)

        layout.addStretch()

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
        
        # if force:
        view.exec()
        

    @Slot()
    def _open_autosaves(self) -> None:
        dlg = AutosaveDialog(self)
        dlg.exec()

    @Slot()
    def _load_autosave_chain(self, chain_desc: list[dict]) -> None:
        """Apply a chain loaded from an autosave (called by AutosaveDialog)."""
        if self._engine.running:
            resp = QMessageBox.question(
                self, "Load Autosave",
                "Load this autosave? This will overwrite your current loadout.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return

        def mutate():
            self._chain_desc = chain_desc

        self._trigger_restart(mutate=mutate)

    @Slot(dict)
    def _on_settings_applied(self, new_settings: dict) -> None:
        changed = (
            new_settings.get("input_device")  != self._settings.get("input_device") or
            new_settings.get("output_device") != self._settings.get("output_device") or
            new_settings.get("samplerate")    != self._settings.get("samplerate") or
            new_settings.get("blocksize")     != self._settings.get("blocksize") or
            new_settings.get("exclusive_mode")     != self._settings.get("exclusive_mode")
        )
        self._settings = new_settings

        if self._engine.running and changed:
            log.info("Settings changed while engine running — triggering restart.")

            def mutate():
                self._settings = new_settings
                save_settings(new_settings)
            
            self._trigger_restart(mutate=mutate)
        elif not self._engine.running and self._settings.get("autostart"):
            self._start_engine()

        self._install_hotkeys()

    # ── Global hotkeys (RegisterHotKey / WM_HOTKEY) ───────────────────────────
    #
    # RegisterHotKey requires a Win32 HWND to receive WM_HOTKEY messages.
    # We use the main window's native handle (self.winId()), which is always
    # valid while the window exists — even when hidden to tray.
    #
    # WM_HOTKEY is NOT delivered via Qt's event system; it arrives through
    # the Win32 message pump. We intercept it by overriding nativeEvent().
    #
    # NOTE (bundle): no special PyInstaller flags needed — RegisterHotKey is
    # a standard win32api call, no extra DLLs involved.

    def _install_hotkeys(self) -> None:
        """
        Unregister all current hotkeys, then re-register from settings.
        Safe to call multiple times (settings applied, startup).
        No-ops gracefully when pywin32 is unavailable.
        """
        if not _WIN32_AVAILABLE:
            return

        self._unregister_all_hotkeys()

        hk     = self._settings.get("hotkeys") or {}
        hwnd   = int(self.winId())

        def _register(hk_id: int, key_string: str, action_tag: str) -> None:
            if not key_string:
                return
            mods, vk = _parse_key_string(key_string)
            if vk is None:
                log.warning("Hotkey: could not parse key string %r — skipped.", key_string)
                return
            try:
                win32gui.RegisterHotKey(hwnd, hk_id, mods, vk)  
                self._hotkey_id_map[hk_id] = action_tag
                log.debug("Registered hotkey id=%d %r → %r", hk_id, key_string, action_tag)
            except Exception as e:
                log.warning(
                    "Could not register hotkey %r (id=%d): %s — "
                    "another app may own this combo.",
                    key_string, hk_id, e,
                )

        _register(_HK_ID_MUTE,  hk.get("mute",  ""), "mute")
        _register(_HK_ID_START, hk.get("start", ""), "start")
        _register(_HK_ID_STOP,  hk.get("stop",  ""), "stop")

        known_presets = set(self._presets.keys())
        for offset, (key_string, preset_name) in enumerate(
            (hk.get("presets") or {}).items()
        ):
            if preset_name not in known_presets:
                log.warning(
                    "Hotkey: preset %r not found — binding %r skipped.",
                    preset_name, key_string,
                )
                continue
            hk_id = _HK_PRESET_BASE + offset
            _register(hk_id, key_string, f"preset:{preset_name}")

    def _unregister_all_hotkeys(self) -> None:
        if not _WIN32_AVAILABLE:
            return
        hwnd = int(self.winId())
        for hk_id in list(self._hotkey_id_map):
            try:
                win32gui.UnregisterHotKey(hwnd, hk_id)
            except Exception:
                pass
        self._hotkey_id_map.clear()

    def nativeEvent(self, event_type: bytes, message) -> tuple[bool, int]:
        """
        Intercept WM_HOTKEY messages from the Win32 message pump.
        Qt does not translate these into key events, so we handle them here.
        """
        WM_HOTKEY = 0x0312
        if _WIN32_AVAILABLE and event_type == b"windows_generic_MSG":
            # message is a sip.voidptr; cast to access wParam (hotkey id).
            import ctypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                hk_id = msg.wParam
                self._on_hotkey_fired(hk_id)
                return True, 0
        return super().nativeEvent(event_type, message)

    def _on_hotkey_fired(self, hk_id: int) -> None:
        action = self._hotkey_id_map.get(hk_id)
        if action is None:
            return

        if action == "mute":
            self._on_mute_toggle()

        elif action == "start":
            if not self._engine.running:
                self._start_engine()

        elif action == "stop":
            if self._engine.running:
                self._stop_engine()

        elif action.startswith("preset:"):
            preset_name = action[len("preset:"):]
            # Check existence against live preset dict — not the settings snapshot.
            if preset_name not in self._presets:
                log.warning(
                    "Hotkey: preset %r no longer exists — ignoring.", preset_name
                )
                return
            # Hotkeys are intentional — apply without confirmation dialog.
            self._apply_preset_by_name(preset_name)


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
                exclusive_mode=self._settings.get("exclusive_mode", False),
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
        self._restart_attempts = 0     # device recovered successfully — reset watchdog counter
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
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

        write_autosave(self._chain_desc, self._settings.get("max_autosaves", 0))

        self._on_engine_stopped()

    def _on_engine_stopped(self) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._xrun_lbl.setText("xruns: 0")
        # Device labels stay populated with configured names — they are static
        # references from settings; blanking them while stopped is confusing.
        self._refresh_device_labels()

    def _on_stream_died(self) -> None:
        """
        Common handler for both watchdog paths:
          A) stream_died flag — stream finished unexpectedly (device reclaim, loss)
          B) callback stall  — stream reports running but callback stopped firing
             (WASAPI driver lock, plugin deadlock, hardware buffer spin)

        Tracks consecutive attempts to prevent an infinite restart loop when the
        device is persistently unavailable (e.g. still hung after recovery attempt).
        _restart_attempts is reset to 0 by _on_engine_started() on a clean start.
        """
        self._restart_attempts += 1

        if self._restart_attempts > _MAX_RESTART_ATTEMPTS:
            log.error(
                "Engine failed to recover after %d consecutive attempts — giving up.",
                _MAX_RESTART_ATTEMPTS,
            )
            self._restart_attempts = 0
            QMessageBox.critical(
                self, "MicHost",
                f"Audio engine could not recover after {_MAX_RESTART_ATTEMPTS} attempts.\n\n"
                "Please check your audio device and restart the app."
            )
            return

        log.warning(
            "Engine recovery attempt %d/%d — calling _trigger_restart().",
            self._restart_attempts, _MAX_RESTART_ATTEMPTS,
        )
        self._trigger_restart()

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
            
            driver_ms = self._engine.stream_info.get("actual_output_latency_ms", 0)
            mode = "private" if self._engine.stream_info.get("exclusive_mode") else "shared"
            self._in_latency_lbl.setText(f"Driver buffer: {driver_ms:.1f} ms ({mode})")

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

    def _trigger_restart(self, mutate: callable = None) -> None:
        """
        Universal restart — the single path for all structural changes and
        reconfiguration. Follows the mandatory 9-step order.
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

            if mutate is not None:
                mutate()

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

        def mutate():
            idx = [n for n, _ in available].index(choice)
            path = available[idx][1]
            self._chain_desc.append({
                "path": path,
                "name": os.path.splitext(os.path.basename(path))[0],
                "bypassed": False,
                "raw_state": None,
            })
        
        self._trigger_restart(mutate=mutate)

    @Slot(int)
    def _on_move_up(self, index: int) -> None:
        if index <= 0:
            return

        def mutate():
            self._chain_desc.insert(index - 1, self._chain_desc.pop(index))
        
        self._trigger_restart(mutate=mutate)

    @Slot(int)
    def _on_move_down(self, index: int) -> None:
        if index >= len(self._chain_desc) - 1:
            return

        def mutate():
            self._chain_desc.insert(index + 1, self._chain_desc.pop(index))
        
        self._trigger_restart(mutate=mutate)

    @Slot(int, bool)
    def _on_bypass_toggled(self, index: int, bypassed: bool) -> None:
        if 0 <= index < len(self._chain_desc):
            self._chain_desc[index]["bypassed"] = bypassed
        # Live command — no restart needed
        self._engine.set_bypass(index, bypassed)

    @Slot(int)
    def _on_remove_plugin(self, index: int) -> None:
        if 0 <= index < len(self._chain_desc):
            def mutate():
                self._chain_desc.pop(index)

            self._trigger_restart(mutate=mutate)

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
        self._preset_combo.addItem("-- Select a preset --")
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
                f"Load preset '{name}'? This will overwrite your current loadout.",
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

        def mutate():
            self._chain_desc = load_preset(os.path.join(PRESETS_DIR, f"{name}.json")).get("chain", [])

        self._trigger_restart(mutate=mutate)

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
        
        def mutate():
            self._chain_desc = []
            self._save_preset(data)

        self._trigger_restart(mutate=mutate)

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

        def mutate():
            self._save_preset(data)

        self._trigger_restart(mutate=mutate)

    def _save_preset(self, data : dict) -> None:
        save_preset(data["name"], data["chain"], PRESETS_DIR)

    @Slot()
    def _delete_preset(self) -> None:
        name = self._current_preset_name()
        if (name == "-- Select a preset --"):
            QMessageBox.information(self, "MicHost", "Cannot delete this.")
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
        # Watchdog A: stream_died — finished_callback flagged an unexpected death
        # (WASAPI exclusive reclaim, device loss, driver event).
        if self._engine.stream_died and not self._restarting:
            log.warning("Watchdog A: stream_died detected — auto-restarting engine.")
            self._engine.stream_died = False
            QTimer.singleShot(0, self._on_stream_died)
            return

        # Watchdog B: callback heartbeat stall — the stream reports as running
        # but the callback has stopped making progress (WASAPI driver lock,
        # plugin deadlock, hardware buffer spin). Detected by a monotonic
        # timestamp written at step 0 of every callback invocation.
        # Only arm this check once the engine is running and has had at least
        # one callback fire (last_callback_time > 0).
        if (
            self._engine.running
            and not self._restarting
            and self._engine.last_callback_time > 0
            and (time.monotonic() - self._engine.last_callback_time) > _CALLBACK_STALL_TIMEOUT
        ):
            log.warning(
                "Watchdog B: callback stall detected (%.2fs since last heartbeat) "
                "— auto-restarting engine.",
                time.monotonic() - self._engine.last_callback_time,
            )
            QTimer.singleShot(0, self._on_stream_died)
            return


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

        # Save session + settings to disk
        save_session(self._chain_desc, SESSION_PATH)
        save_settings(self._settings)

        self._unregister_all_hotkeys()

        log.info("Shutdown complete.")
        event.accept()

# ── Autosave browser dialog ───────────────────────────────────────────────────

class AutosaveDialog(QDialog):
    """
    Modal dialog for browsing and loading autosaves.

    Shows a sortable list of autosave entries (newest-first by default).
    User selects one entry and clicks Load, or cancels.
    Loading delegates back to MainWindow._load_autosave_chain().
    """

    def __init__(self, parent: "MainWindow"):
        super().__init__(parent)
        self._main = parent
        self._sort_ascending = False   # newest-first by default
        self._entries: list[dict] = [] # [{"path": str, "timestamp": datetime}]

        self.setWindowTitle("Autosaves")
        self.setMinimumSize(460, 340)
        self.setModal(True)

        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # ── Header row: count label + sort toggle ─────────────────────────────
        header_row = QHBoxLayout()

        self._count_lbl = QLabel()
        self._count_lbl.setProperty("class", "dim")
        header_row.addWidget(self._count_lbl)

        header_row.addStretch()

        self._sort_btn = QPushButton("Date ▼")
        self._sort_btn.setFixedWidth(72)
        self._sort_btn.setFlat(True)
        self._sort_btn.setStyleSheet("font-size: 11px; color: #9ca3af;")
        self._sort_btn.clicked.connect(self._toggle_sort)
        header_row.addWidget(self._sort_btn)

        root.addLayout(header_row)

        # ── List ──────────────────────────────────────────────────────────────
        from PySide6.QtWidgets import QListWidget
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_load)
        root.addWidget(self._list, stretch=1)

        # ── Buttons ───────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        root.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._load_btn = QPushButton("Load")
        self._load_btn.setEnabled(False)
        self._load_btn.setFixedWidth(80)
        self._load_btn.clicked.connect(self._on_load)
        btn_row.addWidget(self._load_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        root.addLayout(btn_row)

    def _refresh(self) -> None:
        """Reload autosave list from disk and repopulate the widget."""
        self._entries = list_autosaves()   # always returns newest-first
        if self._sort_ascending:
            self._entries = list(reversed(self._entries))
        self._repopulate()

    def _repopulate(self) -> None:
        from PySide6.QtWidgets import QListWidgetItem
        self._list.clear()

        n = len(self._entries)
        if n == 0:
            self._count_lbl.setText("No autosaves yet")
        else:
            self._count_lbl.setText(f"{n} autosave{'s' if n != 1 else ''}")

        for entry in self._entries:
            ts = entry["timestamp"]
            label = ts.strftime("%Y-%m-%d   %H:%M:%S")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry["path"])
            self._list.addItem(item)

        self._load_btn.setEnabled(False)

    def _toggle_sort(self) -> None:
        self._sort_ascending = not self._sort_ascending
        arrow = "▲" if self._sort_ascending else "▼"
        self._sort_btn.setText(f"Date {arrow}")
        self._entries = list(reversed(self._entries))
        self._repopulate()

    def _on_selection_changed(self) -> None:
        self._load_btn.setEnabled(bool(self._list.selectedItems()))

    def _on_load(self) -> None:
        items = self._list.selectedItems()
        if not items:
            return

        path = items[0].data(Qt.UserRole)
        try:
            chain = load_autosave(path)
        except ValueError as e:
            QMessageBox.warning(self, "Autosave Error", str(e))
            return

        self.accept()
        self._main._load_autosave_chain(chain)


# ── Module-level helper for keybinds ──────────────────────────────────

_QT_MOD_MAP = {
    "ctrl":  win32con.MOD_CONTROL if _WIN32_AVAILABLE else 0,
    "shift": win32con.MOD_SHIFT   if _WIN32_AVAILABLE else 0,
    "alt":   win32con.MOD_ALT     if _WIN32_AVAILABLE else 0,
    "meta":  win32con.MOD_WIN     if _WIN32_AVAILABLE else 0,
}

# fmt: off
_VK_MAP: dict[str, int] = {
    # Function keys
    "f1":  0x70, "f2":  0x71, "f3":  0x72,  "f4":  0x73,
    "f5":  0x74, "f6":  0x75, "f7":  0x76,  "f8":  0x77,
    "f9":  0x78, "f10": 0x79, "f11": 0x7A,  "f12": 0x7B,
    # Alphabet
    **{chr(c): ord(chr(c).upper()) for c in range(ord("a"), ord("z") + 1)},
    # Digits (main row)
    **{str(d): ord(str(d)) for d in range(10)},
    # Numpad digits
    "num+0": 0x60, "num+1": 0x61, "num+2": 0x62, "num+3": 0x63,
    "num+4": 0x64, "num+5": 0x65, "num+6": 0x66, "num+7": 0x67,
    "num+8": 0x68, "num+9": 0x69,
    # Numpad operators — Qt emits these as "Num+*", "Num++", etc.
    "num+*": 0x6A, "num++": 0x6B, "num+-": 0x6D,
    "num+.": 0x6E, "num+/": 0x6F,
    # Navigation / misc
    "home":   0x24, "end":    0x23, "pgup":  0x21, "pgdown": 0x22,
    "ins":    0x2D, "space":  0x20,
    "left":   0x25, "up":     0x26, "right": 0x27, "down":   0x28,
    # Punctuation — US layout
    ";":  0xBA, "=":  0xBB, ",":  0xBC, "-":  0xBD,
    ".":  0xBE, "/":  0xBF, "`":  0xC0, "[":  0xDB,
    "\\": 0xDC, "|":  0xDC,             # | is Shift+\ — same VK
    "]":  0xDD, "'":  0xDE,
    # Main keyboard operators (when used without Num+ prefix)
    "*":  0x6A, "+":  0x6B,
}
# fmt: on

def _parse_key_string(key_string: str) -> tuple[int, int | None]:
    """
    Parse a Qt PortableText key string into (win32_modifiers, vk_code).
    Returns (0, None) if the key part cannot be resolved.

    Handles the Num+X family specially: Qt emits numpad keys as e.g. "Num+*"
    or "Ctrl+Num+5". Splitting naively on "+" would destroy those tokens, so
    we reassemble any "Num" + following token before doing modifier extraction.

    Examples:
      "Ctrl+M"      → (MOD_CONTROL, 0x4D)
      "F5"          → (0, 0x74)
      "Shift+F9"    → (MOD_SHIFT, 0x78)
      "Ctrl+Alt+X"  → (MOD_CONTROL | MOD_ALT, 0x58)
      "Num+*"       → (0, 0x6A)
      "Ctrl+Num+5"  → (MOD_CONTROL, 0x65)
    """
    # Reassemble Num+X tokens before splitting on modifiers.
    # "Ctrl+Num+5" → split gives ["Ctrl", "Num", "5"]; we want ["Ctrl", "Num+5"].
    raw_parts = key_string.split("+")
    parts: list[str] = []
    i = 0
    while i < len(raw_parts):
        if raw_parts[i].lower() == "num" and i + 1 < len(raw_parts):
            parts.append(f"Num+{raw_parts[i + 1]}")
            i += 2
        else:
            parts.append(raw_parts[i])
            i += 1

    mods = 0
    key  = ""
    for part in parts:
        lower = part.lower()
        if lower in _QT_MOD_MAP:
            mods |= _QT_MOD_MAP[lower]
        else:
            key = lower  # last non-modifier token is the key

    vk = _VK_MAP.get(key)
    return mods, vk