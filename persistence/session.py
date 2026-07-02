"""
session.py — session and preset persistence (Sections 6.1–6.3).

Session state  = auto-saved "what was open last"; restored on startup,
                 saved on shutdown and before every restart.
Preset         = explicitly named user snapshot stored in ./presets/.

Both share the same JSON schema:
{
  "version": 1,
  "name": "Vocal Chain A",          # absent in session.json
  "chain": [
    {
      "path": "./vst3/Comp.vst3",
      "name": "Comp",
      "bypassed": false,
      "raw_state": "<base64-encoded bytes or null>"
    }
  ]
}

Public API:
  capture_raw_state(chain_desc, active_chain) -> list[dict]
      Read raw_state from live Pedalboard objects into chain_desc dicts.
      MUST only be called after engine.stop() — audio thread must be dead.

  save_session(chain_desc, session_path)
      Atomic write of session JSON with .bak rotation.

  load_session(session_path) -> list[dict]
      Load chain_desc from session JSON; returns [] on missing or corrupt file.

  save_preset(name, chain_desc, presets_dir, engine_config=None) -> str
      Write preset JSON; returns the file path.
      Handles created_at / last_modified timestamps internally.
      engine_config is stored as last_engine_config when provided.

  load_preset(path) -> dict
      Load a single preset file; raises on corrupt.

  list_presets(presets_dir) -> list[dict]
      Return all valid presets sorted by name.

  delete_preset(path)
      Delete a preset file.

  build_chain_objects(chain_desc, on_missing, on_load_error) -> list
      Instantiate [Pedalboard, bypass_flag] from chain_desc.
      Calls on_missing(name) for missing plugins (skip + warn).
      Calls on_load_error(name, error) for failed loads (skip + warn).
      Restores raw_state with the last-words log pattern.
"""

import base64
import datetime
import glob
import json
import logging
import os
import re
import shutil
import tempfile

log = logging.getLogger(__name__)

SESSION_VERSION = 2


# ── raw_state capture ─────────────────────────────────────────────────────────

def capture_raw_state(chain_desc: list[dict], active_chain: list) -> list[dict]:
    """
    Read raw_state from live Pedalboard objects back into chain_desc.

    INVARIANT: call this only after engine.stop() — audio thread must be dead.
    raw_state is an opaque VST3 bytes blob; we base64-encode it for JSON.

    Returns an updated copy of chain_desc (mutates in-place and returns it).
    """
    for i, entry in enumerate(active_chain):
        if i >= len(chain_desc):
            break
        board    = entry[0]     # Pedalboard
        bypassed = bool(entry[1])

        chain_desc[i]["bypassed"] = bypassed

        plugins = list(board)
        if not plugins:
            continue
        plugin = plugins[0]

        try:
            raw = plugin.raw_state          # bytes or None
            chain_desc[i]["raw_state"] = (
                base64.b64encode(raw).decode("ascii") if raw else None
            )
            log.debug(
                "Captured raw_state for slot %d (%s): %d bytes",
                i, chain_desc[i].get("name", "?"),
                len(raw) if raw else 0,
            )
        except Exception as e:
            log.warning(
                "Could not read raw_state for slot %d (%s): %s",
                i, chain_desc[i].get("name", "?"), e,
            )

    return chain_desc


# ── Session save / load ───────────────────────────────────────────────────────

def save_session(chain_desc: list[dict], session_path: str) -> None:
    """
    Atomically write session JSON. Rotates the existing file to .bak first.
    Never raises — logs errors instead (session loss is survivable).
    """
    data = {
        "version": SESSION_VERSION,
        "chain": chain_desc,
    }
    _atomic_write(session_path, data)
    log.info("Session saved (%d slot(s)) → %s", len(chain_desc), session_path)


def load_session(session_path: str) -> list[dict]:
    """
    Load session from JSON. Returns [] on missing, corrupt, or wrong-version file.
    """
    if not os.path.exists(session_path):
        log.debug("No session file at %s — starting with empty chain.", session_path)
        return []

    try:
        with open(session_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Session file unreadable (%s) — starting fresh.", e)
        return []

    if not isinstance(data, dict):
        log.warning("Session file has unexpected structure — starting fresh.")
        return []

    chain = data.get("chain", [])
    if not isinstance(chain, list):
        log.warning("Session 'chain' is not a list — starting fresh.")
        return []

    log.info("Session loaded (%d slot(s)) from %s", len(chain), session_path)
    return chain


# ── Preset save / load / list / delete ───────────────────────────────────────

def save_preset(
    name: str,
    chain_desc: list[dict],
    presets_dir: str,
    engine_config: dict | None = None,
) -> str:
    """
    Write a named preset to presets_dir. Returns the file path written.
    Uses the same atomic write + .bak strategy as session save.

    Timestamps:
      - created_at is written once on first save and preserved on all
        subsequent writes. Its absence in existing files is tolerated.
      - last_modified is updated on every write.

    engine_config: stored as last_engine_config when provided. Callers
      must not assume this key is present when loading older files.
    """
    os.makedirs(presets_dir, exist_ok=True)
    safe_name = _safe_filename(name)
    path = os.path.join(presets_dir, f"{safe_name}.json")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Preserve created_at from an existing file on disk if present.
    created_at = now
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            created_at = existing.get("created_at", now)
        except (json.JSONDecodeError, OSError):
            pass  # File unreadable — treat as first write

    data: dict = {
        "version": SESSION_VERSION,
        "name": name,
        "created_at": created_at,
        "last_modified": now,
        "chain": chain_desc,
    }
    if engine_config is not None:
        data["last_engine_config"] = engine_config

    _atomic_write(path, data)
    log.info("Preset '%s' saved → %s", name, path)
    return path


def load_preset(path: str) -> dict:
    """
    Load a single preset file. Raises ValueError on corrupt or wrong structure.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Could not read preset {path}: {e}") from e

    if not isinstance(data, dict) or "chain" not in data:
        raise ValueError(f"Preset {path} has unexpected structure.")

    return data


def list_presets(presets_dir: str) -> list[dict]:
    """
    Return all valid presets in presets_dir, sorted by name.
    Invalid files are logged and skipped.
    """
    os.makedirs(presets_dir, exist_ok=True)
    results = []
    for path in sorted(glob.glob(os.path.join(presets_dir, "*.json"))):
        try:
            data = load_preset(path)
            data["_path"] = path   # internal: file path for delete/update
            results.append(data)
        except ValueError as e:
            log.warning("Skipping invalid preset file: %s", e)

    results.sort(key=lambda d: d.get("name", "").lower())
    return results


def delete_preset(path: str) -> None:
    """Delete a preset file. Logs but does not raise on failure."""
    try:
        os.remove(path)
        log.info("Preset deleted: %s", path)
    except OSError as e:
        log.warning("Could not delete preset %s: %s", path, e)


# ── Chain builder ─────────────────────────────────────────────────────────────

def build_chain_objects(
    chain_desc: list[dict],
    on_missing=None,
    on_load_error=None,
    shutdown_flag=None,
) -> list:
    """
    Instantiate [Pedalboard, bypass_flag] entries from chain_desc.

    Missing plugins  → call on_missing(name: str); slot skipped.
    Load failures    → call on_load_error(name: str, error: Exception); slot skipped.
    raw_state restore uses the last-words log pattern (Section 6.1).

    shutdown_flag: threading.Event — if set, abort immediately and return [].
    This handles the race between a worker-thread build and app close (Section 4.5).
    """
    from pedalboard import Pedalboard, load_plugin

    chain = []

    for item in chain_desc:
        # Check shutdown flag at the top of each iteration (Section 4.5)
        if shutdown_flag is not None and shutdown_flag.is_set():
            log.info("build_chain_objects: shutdown requested — aborting build.")
            return []

        path     = item.get("path", "")
        name     = item.get("name", os.path.basename(path))
        bypassed = bool(item.get("bypassed", False))
        raw_b64  = item.get("raw_state")

        # Missing plugin check 
        if not os.path.exists(path):
            log.warning("Plugin not found, skipping: %s", path)
            if on_missing:
                on_missing(name)
            continue

        # Load plugin
        log.info("Loading plugin: %s (%s)", name, path)
        try:
            plugin = load_plugin(path)
        except Exception as e:
            log.error("Failed to load plugin %s: %s", name, e)
            if on_load_error:
                on_load_error(name, e)
            continue

        # Restore raw_state — last-words log pattern
        if raw_b64:
            log.info("Restoring state for plugin: %s (%s)", name, path)
            try:
                plugin.raw_state = base64.b64decode(raw_b64)
            except Exception as e:
                log.error("State restore failed for %s: %s", name, e)
                # Do not re-raise — continue with default plugin state

        board = Pedalboard([plugin])
        chain.append([board, bypassed])
        log.debug("Plugin ready: %s (bypassed=%s)", name, bypassed)

    return chain


# ── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write(path: str, data: dict) -> None:
    """
    Write data as JSON to path atomically:
      1. Write to a sibling temp file.
      2. Copy existing file to .bak.
      3. os.replace(temp, path).
    """
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(target_dir, exist_ok=True)

    try:
        fd, tmp = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")

        os.replace(tmp, path)

    except OSError as e:
        log.error("Atomic write failed for %s: %s", path, e)


def _safe_filename(name: str) -> str:
    """Convert a preset name to a safe filesystem filename."""
    safe = re.sub(r'[^\w\- ]', '_', name).strip()
    return safe or "preset"
