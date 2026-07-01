"""
autosave.py — per-preset autosave stubs (persistence package).

Wiring is established in this pass; implementation is deferred.

When implemented, write_autosave will write one autosave per engine halt
under ./autosaves/<safe_preset_name>/autosave_<YYYYMMDD_HHMMSS>.json and
prune to max_autosaves if set (0 = unlimited).

Public API:
  write_autosave(preset_name, chain_desc, app_version, engine_config) -> str
      No-op stub. Returns empty string.

  load_latest_autosave(preset_name) -> list[dict]
      No-op stub. Returns [].

  list_autosaves(preset_name) -> list[str]
      No-op stub. Returns [].
"""


def write_autosave(
    preset_name: str,
    chain_desc: list[dict],
    app_version: str,
    engine_config: dict,
) -> str:
    """Stub — no-op for this pass. Returns empty string."""
    return ""


def load_latest_autosave(preset_name: str) -> list[dict]:
    """Stub — returns [] for this pass."""
    return []


def list_autosaves(preset_name: str) -> list[str]:
    """Stub — returns [] for this pass."""
    return []
