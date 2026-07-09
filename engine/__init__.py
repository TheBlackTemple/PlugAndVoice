"""
engine — headless audio engine for PlugAndVoice.

Public surface:
  AudioEngine   — the engine class (core.py)
  meter_of      — buffer metering utility (metering.py)
"""

from .core import AudioEngine
from .metering import meter_of

__all__ = ["AudioEngine", "meter_of"]
