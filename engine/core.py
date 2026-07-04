"""
core.py — headless AudioEngine.

Owns:
  - the sounddevice stream
  - command_q  (GUI → audio, drained each callback)
  - meter_q    (audio → GUI, latest-value-wins)
  - active_chain reference (list of [Pedalboard, bypass_flag])
  - xrun_count (GIL-atomic int, written by audio thread, read by GUI)

Public interface (the contract the GUI depends on):
  start()               — open and start the stream; raises on failure
  stop()                — stop and close the stream; blocks until audio thread exits
  set_chain(chain)      — install a freshly built chain; only called from start/restart
  set_mute(bool)        — live command via command_q
  set_bypass(int, bool) — live command via command_q
  xrun_count            — readable int
  meter_q               — collections.deque(maxlen=1)
  stream_info           — dict with device/format/samplerate/channels once started
  stream_died           — bool set by finished_callback when stream dies unexpectedly
"""

import queue
import logging
import numpy as np
import sounddevice as sd

from collections import deque
from .metering import meter_of

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Array orientation helpers (Section 5.4 — proven pattern, do not alter)
# ---------------------------------------------------------------------------

def _to_pb_mono(indata: np.ndarray) -> np.ndarray:
    """sounddevice mono input (frames, 1) → Pedalboard stereo (2, frames)."""
    return np.tile(indata.T, (2, 1))


def _to_pb_stereo(indata: np.ndarray) -> np.ndarray:
    """sounddevice stereo input (frames, 2) → Pedalboard stereo (2, frames)."""
    return indata.T.copy()


def _from_pb(buf: np.ndarray) -> np.ndarray:
    """Pedalboard stereo (2, frames) → sounddevice output (frames, 2)."""
    return buf.T


# ---------------------------------------------------------------------------
# Command types
# ---------------------------------------------------------------------------

class _CmdMute:
    __slots__ = ("muted",)
    def __init__(self, muted: bool):
        self.muted = muted


class _CmdBypass:
    __slots__ = ("index", "bypassed")
    def __init__(self, index: int, bypassed: bool):
        self.index = index
        self.bypassed = bypassed


# ---------------------------------------------------------------------------
# AudioEngine
# ---------------------------------------------------------------------------

class AudioEngine:
    def __init__(self):
        # Public readable state
        self.xrun_count: int = 0          # GIL-atomic; written by audio thread only
        self.meter_q: deque = deque(maxlen=1)
        self.stream_info: dict = {}
        self.stream_died: bool = False     # set by finished_callback; cleared on start/stop

        # Internal state
        self._stream: sd.Stream | None = None
        self._command_q: queue.SimpleQueue = queue.SimpleQueue()

        # Chain: list of [Pedalboard, bypass_flag].
        # Snapshotted by reference each callback (GIL-atomic read).
        self._active_chain: list = []

        # Flags written only by the main thread before start/stop;
        # read by the callback — no lock needed (GIL + assignment atomicity).
        self._muted: bool = False

        # Captured at stream open; read by callback (immutable during streaming).
        self._samplerate: float = 48000.0
        self._in_channels: int = 1

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_chain(self, chain: list) -> None:
        """
        Install a freshly built chain.
        chain: list of [Pedalboard, bypass_flag]
        Must only be called before start() or as part of a stop/rebuild/start cycle.
        Never injected into a running callback.
        """
        self._active_chain = chain

    def set_mute(self, muted: bool) -> None:
        """Post a mute command — applied at the next callback boundary."""
        self._command_q.put(_CmdMute(muted))

    def set_bypass(self, index: int, bypassed: bool) -> None:
        """Post a per-slot bypass command — applied at the next callback boundary."""
        self._command_q.put(_CmdBypass(index, bypassed))

    def start(
        self,
        input_device,        # sounddevice device index or name
        output_device,       # sounddevice device index or name
        samplerate: float,
        blocksize: int,
        exclusive_mode: bool = False,   # WASAPI Private Mode; ignored on non-WASAPI
    ) -> None:
        """
        Open and start the audio stream.
        Raises on any failure — caller surfaces the error to the user.

        exclusive_mode=True requests WASAPI exclusive access on both input and
        output devices.  This lowers latency and prevents other applications from
        accessing the raw device while the engine is running.  Has no effect when
        the selected devices are not on the WASAPI host API.
        """
        self.stream_died = False
        if self._stream is not None:
            raise RuntimeError("Engine already running; call stop() first.")

        in_channels = self._probe_input_channels(input_device, samplerate)

        # Validate before opening (Section 5.3).
        # Note: extra_settings (WasapiSettings) is not supported by
        # check_input/output_settings — we pass it only at stream open below.
        sd.check_input_settings(
            device=input_device,
            channels=in_channels,
            samplerate=samplerate,
        )
        sd.check_output_settings(
            device=output_device,
            channels=2,
            samplerate=samplerate,
        )

        self._samplerate = float(samplerate)
        self._in_channels = in_channels

        # Build WASAPI exclusive settings if requested.
        # sd.WasapiSettings is only present in sounddevice builds with WASAPI
        # support (standard on Windows); guard defensively so imports on
        # non-Windows environments don't crash.
        extra_settings = None
        if exclusive_mode:
            if hasattr(sd, "WasapiSettings"):
                wasapi_cfg = sd.WasapiSettings(exclusive=True)
                extra_settings = (wasapi_cfg, wasapi_cfg)
                log.info("WASAPI exclusive mode (Private Mode) requested.")
            else:
                log.warning(
                    "exclusive_mode=True but sd.WasapiSettings not available "
                    "in this sounddevice build — falling back to shared mode."
                )

        stream_kwargs = dict(
            device=(input_device, output_device),
            samplerate=samplerate,
            blocksize=blocksize,
            channels=(in_channels, 2),
            dtype="float32",
            callback=self._callback,
            finished_callback=self._on_stream_finished,
        )
        if extra_settings is not None:
            stream_kwargs["extra_settings"] = extra_settings

        self._stream = sd.Stream(**stream_kwargs)

        # Post-open assertion (Section 5.3)
        actual_in = self._stream.channels[0]
        actual_out = self._stream.channels[1]
        if actual_in != in_channels or actual_out != 2:
            self._stream.close()
            self._stream = None
            raise RuntimeError(
                f"Stream channel mismatch: requested ({in_channels}, 2), "
                f"got ({actual_in}, {actual_out})"
            )

        self._stream.start()

        # Populate stream_info for the GUI info blocks
        in_info = sd.query_devices(input_device)
        out_info = sd.query_devices(output_device)
        self.stream_info = {
            "input_device": in_info["name"],
            "output_device": out_info["name"],
            "samplerate": self._stream.samplerate,
            "blocksize": blocksize,
            "in_channels": actual_in,
            "out_channels": actual_out,
            "latency_ms": round(blocksize / samplerate * 1000, 2),
            "exclusive_mode": extra_settings is not None,
        }

        log.info(
            "Engine started — in: %s (%dch) | out: %s (2ch) | "
            "%g Hz | block %d (%.1f ms) | exclusive: %s",
            in_info["name"], actual_in,
            out_info["name"],
            samplerate, blocksize,
            self.stream_info["latency_ms"],
            self.stream_info["exclusive_mode"],
        )

    def stop(self) -> None:
        """
        Stop and close the stream, blocking until the audio thread has exited.
        Safe to call if already stopped.
        """
        if self._stream is None:
            return
        log.info("Engine stopping…")
        self.stream_died = False   # clear before stop so watchdog doesn't fire on manual stop
        self._stream.stop()   # blocks until callback returns for the last time
        self._stream.close()
        self._stream = None
        self.stream_info = {}
        log.info("Engine stopped.")

    @property
    def running(self) -> bool:
        return self._stream is not None and self._stream.active

    # ------------------------------------------------------------------
    # Audio callback (Section 5.5 — proven pattern, shape is mandatory)
    # ------------------------------------------------------------------

    def _callback(
        self,
        indata: np.ndarray,     # (frames, in_channels), float32
        outdata: np.ndarray,    # (frames, 2), float32  — write in-place
        frames: int,
        time_info,
        status,
    ) -> None:

        # 1. xrun monitoring — increment only, never block or print
        if status:
            self.xrun_count += 1

        # 2. Drain commands — fast, non-blocking, O(1) per command
        #    SimpleQueue.get_nowait() raises queue.Empty when empty.
        while True:
            try:
                cmd = self._command_q.get_nowait()
            except Exception:
                break
            self._apply_command(cmd)

        # 3. Snapshot active chain reference (GIL-atomic read)
        chain = self._active_chain

        # 4. Orient input to Pedalboard (channels, frames)
        if self._in_channels == 1:
            buffer = _to_pb_mono(indata)
        else:
            buffer = _to_pb_stereo(indata)

        # Defend against unexpected shape (Section 5.3)
        if buffer.shape[0] != 2:
            log.error("Unexpected buffer shape after orient: %s", buffer.shape)
            outdata[:] = 0
            return

        input_meter = meter_of(buffer)   # pre-chain level

        # 5. Sequential chain with per-slot metering (reset=False is critical)
        plugin_meters = []
        for entry in chain:
            board, bypassed = entry[0], entry[1]
            if not bypassed:
                buffer = board(buffer, self._samplerate, reset=False)
            plugin_meters.append(meter_of(buffer))  # reflects bypass passthrough

        master_meter = meter_of(buffer)  # post-chain, pre-mute

        # 6. Master mute
        out = np.zeros_like(buffer) if self._muted else buffer

        # 7. Orient back to sounddevice (frames, channels)
        outdata[:] = _from_pb(out)

        # 8. Push meter — latest-value-wins, non-blocking
        payload = {
            "input": input_meter,
            "plugins": plugin_meters,
            "master": master_meter,
        }
        # deque(maxlen=1) append is atomic and never blocks
        self.meter_q.append(payload)

    def _apply_command(self, cmd) -> None:
        """
        Apply a command from command_q.
        MUST be O(1). MUST NOT build plugins, allocate heavily, or block.
        Only assigns references and sets flags.
        """
        if isinstance(cmd, _CmdMute):
            self._muted = cmd.muted

        elif isinstance(cmd, _CmdBypass):
            chain = self._active_chain
            idx = cmd.index
            if 0 <= idx < len(chain):
                chain[idx][1] = cmd.bypassed

    def _on_stream_finished(self) -> None:
        # Called by sounddevice's internal thread when the stream stops for any
        # reason — including unexpected device loss or WASAPI exclusive reclaim.
        # If stream_died is already False it means stop() cleared it first,
        # meaning this was a clean/manual stop — don't flag it.
        # If stop() hasn't run yet, stream_died will still be False here
        # (it's only set True below), so we check _stream instead.
        if self._stream is not None and self._stream.active is False:
            # Stream went inactive but we didn't call stop() — unexpected death.
            log.warning("Stream finished unexpectedly — flagging stream_died.")
            self.stream_died = True
        else:
            log.debug("sounddevice stream finished callback fired (clean stop).")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _probe_input_channels(self, device, samplerate: float) -> int:
        """
        Return 1 (mono) or 2 (stereo) for the given input device.
        We prefer to open in the device's native channel count up to stereo.
        If the device reports > 2 channels we still open stereo (we coerce in callback).
        """
        try:
            info = sd.query_devices(device, kind="input")
            native = int(info.get("max_input_channels", 1))
            return min(native, 2)
        except Exception:
            # If we can't query, try mono — the check_input_settings call will
            # raise with a clear error if the device rejects it.
            return 1
