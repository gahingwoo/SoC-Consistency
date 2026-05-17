"""DTS parse-result cache: keyed on (abs_path, mtime_ns, size, soc_name).

Cache directory: ``~/.cache/socc/``

A stale entry is detected via mtime + size change and silently replaced.
Serialisation uses ``pickle`` (internal use only; never untrusted input).
Write failures are silently ignored so the cache is always a pure optimisation
and never a hard dependency.
"""

from __future__ import annotations

import hashlib
import os
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from socc.model import SoC

_CACHE_DIR = Path.home() / ".cache" / "socc"


def _entry_key(file_path: str, soc_name: str) -> str:
    """Compute a cache-entry identifier for *file_path* + *soc_name*."""
    abs_path = os.path.abspath(file_path)
    st       = os.stat(abs_path)
    # sha1 is used here purely as a fast hash for file-system keys;
    # there is no security relevance.
    raw = f"{abs_path}\x00{st.st_mtime_ns}\x00{st.st_size}\x00{soc_name}"
    return hashlib.sha1(raw.encode()).hexdigest()  # noqa: S324


def get_cached_model(file_path: str, soc_name: str) -> "Optional[SoC]":
    """Return a cached :class:`~socc.model.SoC` model, or *None* on miss/stale."""
    try:
        key  = _entry_key(file_path, soc_name)
        path = _CACHE_DIR / f"{key}.pkl"
        if path.exists():
            return pickle.loads(path.read_bytes())  # noqa: S301
    except Exception:  # noqa: BLE001
        pass
    return None


def set_cached_model(file_path: str, soc_name: str, model: "SoC") -> None:
    """Persist *model* into the cache.  Write failures are silently ignored."""
    try:
        key = _entry_key(file_path, soc_name)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.pkl").write_bytes(pickle.dumps(model))
    except Exception:  # noqa: BLE001
        pass


def clear_cache() -> int:
    """Delete all cached entries.  Returns the number of files removed."""
    count = 0
    if _CACHE_DIR.exists():
        for p in _CACHE_DIR.glob("*.pkl"):
            try:
                p.unlink()
                count += 1
            except OSError:
                pass
    return count


def cache_stats() -> dict:
    """Return a dict with cache directory path and entry count."""
    count = 0
    total_bytes = 0
    if _CACHE_DIR.exists():
        for p in _CACHE_DIR.glob("*.pkl"):
            count += 1
            try:
                total_bytes += p.stat().st_size
            except OSError:
                pass
    return {
        "directory": str(_CACHE_DIR),
        "entries":   count,
        "size_kb":   total_bytes // 1024,
    }


# ── Violation-result cache (keyed on file content hash + soc + rule hash) ─────

def _result_key(file_path: str, soc_name: str, rules_hash: str) -> str:
    """Key for the full violation-result cache."""
    abs_path = os.path.abspath(file_path)
    try:
        content  = open(abs_path, "rb").read()
        # sha1 is used here purely as a fast hash key; no security relevance.
        file_hash = hashlib.sha1(content).hexdigest()  # noqa: S324
    except OSError:
        file_hash = "err"
    raw = f"violations\x00{abs_path}\x00{file_hash}\x00{soc_name}\x00{rules_hash}"
    return "v_" + hashlib.sha1(raw.encode()).hexdigest()  # noqa: S324


def get_cached_violations(
    file_path: str,
    soc_name: str,
    rules_hash: str,
):
    """Return cached violations list or *None* on miss.

    ``rules_hash`` should be a short digest of the active rule set so that
    violations are invalidated whenever rules are added or removed.
    """
    try:
        key  = _result_key(file_path, soc_name, rules_hash)
        path = _CACHE_DIR / f"{key}.pkl"
        if path.exists():
            return pickle.loads(path.read_bytes())  # noqa: S301
    except Exception:  # noqa: BLE001
        pass
    return None


def set_cached_violations(
    file_path: str,
    soc_name: str,
    rules_hash: str,
    violations,
) -> None:
    """Persist violation list into the result cache."""
    try:
        key = _result_key(file_path, soc_name, rules_hash)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.pkl").write_bytes(pickle.dumps(violations))
    except Exception:  # noqa: BLE001
        pass

