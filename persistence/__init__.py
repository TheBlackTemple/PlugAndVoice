"""
persistence — session and preset serialisation for MicHost (Sections 6.1–6.3).

Public surface:
  capture_raw_state(chain_desc, active_chain) -> list[dict]
  save_session(chain_desc, session_path)
  load_session(session_path) -> list[dict]
  save_preset(name, chain_desc, presets_dir) -> str
  load_preset(path) -> dict
  list_presets(presets_dir) -> list[dict]
  delete_preset(path)
  build_chain_objects(chain_desc, on_missing, on_load_error, shutdown_flag) -> list
"""

from .session import (
    capture_raw_state,
    save_session,
    load_session,
    save_preset,
    load_preset,
    list_presets,
    delete_preset,
    build_chain_objects,
)

__all__ = [
    "capture_raw_state",
    "save_session",
    "load_session",
    "save_preset",
    "load_preset",
    "list_presets",
    "delete_preset",
    "build_chain_objects",
]
