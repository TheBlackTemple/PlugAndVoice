"""
metering.py — per-buffer level measurement.

Operates on Pedalboard-orientation buffers: (channels, frames), float32.
Returns {"rms": float, "peak": float} in dBFS.
"""

import numpy as np

_FLOOR_DB = -120.0  # silence floor — avoid log10(0)


def _to_db(linear: float) -> float:
    if linear <= 0.0:
        return _FLOOR_DB
    db = 20.0 * np.log10(linear)
    return max(db, _FLOOR_DB)


def meter_of(buf: np.ndarray) -> dict:
    """
    buf: (channels, frames) float32, Pedalboard orientation.
    Returns {"rms": dBFS, "peak": dBFS}.
    """
    if buf.size == 0:
        return {"rms": _FLOOR_DB, "peak": _FLOOR_DB}

    rms_linear = float(np.sqrt(np.mean(buf ** 2)))
    peak_linear = float(np.max(np.abs(buf)))

    return {
        "rms": _to_db(rms_linear),
        "peak": _to_db(peak_linear),
    }
