"""
autosave.py — session autosave (persistence package).

Autosaves are written on every engine halt into <app_root>/autosaves/ as flat JSON
files named autosave_<YYYYMMDD_HHMMSS>.json.  The schema is identical to
session.json (version + chain), so load paths are shared.

Pruning: when max_autosaves > 0, the oldest files beyond the cap are deleted
after each write.  0 means unlimited.

Public API:
  write_autosave(chain_desc, autosaves_dir, max_autosaves) -> str
      Write one autosave file.  Returns the path written, or "" on failure.

  list_autosaves(autosaves_dir) -> list[dict]
      Return [{"path": str, "timestamp": datetime}, ...] newest-first.

  load_autosave(path) -> list[dict]
      Load chain from an autosave file.  Raises ValueError on corrupt/missing.
"""

import datetime
import glob
import json
import logging
import os
import re
import shutil
import tempfile

log = logging.getLogger(__name__)

_FNAME_RE      = re.compile(r"autosave_(\d{8}_\d{6})\.json$")
_FNAME_FMT     = "%Y%m%d_%H%M%S"
_AUTOSAVE_VERSION = 2


# ── Public API ────────────────────────────────────────────────────────────────

def write_autosave(chain_desc: list[dict], autosaves_dir: str, max_autosaves: int = 0) -> str:
    """
    Write one autosave file to autosaves_dir.

    Filename: autosave_<YYYYMMDD_HHMMSS>.json
    After writing, prunes oldest files when max_autosaves > 0.
    Returns the path written, or "" on any failure (logged, never raises).
    """
    os.makedirs(autosaves_dir, exist_ok=True)

    now      = datetime.datetime.now()
    stamp    = now.strftime(_FNAME_FMT)
    filename = f"autosave_{stamp}.json"
    path     = os.path.join(autosaves_dir, filename)

    data = {
        "version": _AUTOSAVE_VERSION,
        "chain":   chain_desc,
    }

    written = _atomic_write(path, data)
    if not written:
        return ""

    log.info("Autosave written (%d slot(s)) → %s", len(chain_desc), path)

    if max_autosaves > 0:
        _prune(autosaves_dir, max_autosaves)

    return path


def list_autosaves(autosaves_dir: str) -> list[dict]:
    """
    Return all autosave entries in autosaves_dir, newest-first.

    Each entry: {"path": str, "timestamp": datetime.datetime}
    Files that don't match the naming pattern are silently ignored.
    """
    if not os.path.isdir(autosaves_dir):
        return []

    entries = []
    for path in glob.glob(os.path.join(autosaves_dir, "autosave_*.json")):
        m = _FNAME_RE.search(os.path.basename(path))
        if not m:
            continue
        try:
            ts = datetime.datetime.strptime(m.group(1), _FNAME_FMT)
        except ValueError:
            continue
        entries.append({"path": path, "timestamp": ts})

    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries


def load_autosave(path: str) -> list[dict]:
    """
    Load chain_desc from an autosave file.

    Raises ValueError on missing, corrupt, or wrong-structure files.
    """
    if not os.path.exists(path):
        raise ValueError(f"Autosave not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Could not read autosave {path}: {e}") from e

    if not isinstance(data, dict) or "chain" not in data:
        raise ValueError(f"Autosave {path} has unexpected structure.")

    chain = data["chain"]
    if not isinstance(chain, list):
        raise ValueError(f"Autosave {path}: 'chain' is not a list.")

    return chain


# ── Internals ─────────────────────────────────────────────────────────────────

def _prune(autosaves_dir: str, max_autosaves: int) -> None:
    """Delete oldest autosave files beyond max_autosaves."""
    entries = list_autosaves(autosaves_dir)  # already newest-first
    to_delete = entries[max_autosaves:] # everything past the cap
    for entry in to_delete:
        try:
            os.remove(entry["path"])
            log.debug("Pruned autosave: %s", entry["path"])
        except OSError as e:
            log.warning("Could not prune autosave %s: %s", entry["path"], e)


def _atomic_write(path: str, data: dict) -> bool:
    """
    Write data as JSON to path atomically:
      1. Write to a sibling temp file.
      2. Copy existing file to .bak.
      3. os.replace(temp, path).

    Returns True on success, False on failure (logged).
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
        return True

    except OSError as e:
        log.error("Atomic write failed for %s: %s", path, e)
        return False
