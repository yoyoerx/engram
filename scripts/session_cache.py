"""Session state cache for Engram hooks.

Tracks queries, returned chunk_ids, exchange counts, and stored content hashes
within a single Claude Code session.

Cache file: ~/.engram/sessions/{session_id}.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from engram_mcp.config import ENGRAM_SESSIONS_DIR

_SESSIONS_DIR = Path(ENGRAM_SESSIONS_DIR)
_MAX_QUERY_HASHES = 20    # ring buffer — keep last N query fingerprints
_MAX_CHUNK_IDS = 200      # cap to avoid unbounded growth in long sessions
_MAX_STORED_HASHES = 150  # cross-run dedup within a session


def _path(session_id: str) -> Path:
    return _SESSIONS_DIR / f"{session_id}.json"


def _defaults() -> dict:
    return {
        "query_hashes": [],
        "seen_chunk_ids": [],
        "stored_content_hashes": [],   # cross-run dedup for auto_store
        "exchange_count": 0,           # Stop events since last auto_store run
        "last_auto_store_msg_count": 0, # transcript length at last auto_store run
        "transcript_path": "",         # last known transcript path (for compact hook)
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def load_cache(session_id: str) -> dict:
    """Load session cache from disk. Returns empty structure if missing or corrupt."""
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(session_id)
    try:
        if p.exists():
            on_disk = json.loads(p.read_text(encoding="utf-8"))
            # Merge in any new default keys so old caches stay compatible
            defaults = _defaults()
            for k, v in defaults.items():
                on_disk.setdefault(k, v)
            return on_disk
    except Exception:
        pass
    return _defaults()


def save_cache(session_id: str, cache: dict) -> None:
    """Persist session cache to disk. Silently ignores write errors."""
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _path(session_id).write_text(
            json.dumps(cache, ensure_ascii=True), encoding="utf-8"
        )
    except Exception:
        pass


def has_seen_query(cache: dict, query_hash: str) -> bool:
    return query_hash in cache.get("query_hashes", [])


def record_query(cache: dict, query_hash: str, chunk_ids: list[str]) -> dict:
    """Add query hash and new chunk_ids to the cache (in-place). Returns updated cache."""
    hashes = cache.setdefault("query_hashes", [])
    hashes.append(query_hash)
    if len(hashes) > _MAX_QUERY_HASHES:
        cache["query_hashes"] = hashes[-_MAX_QUERY_HASHES:]

    seen = cache.setdefault("seen_chunk_ids", [])
    seen.extend(chunk_ids)
    if len(seen) > _MAX_CHUNK_IDS:
        cache["seen_chunk_ids"] = seen[-_MAX_CHUNK_IDS:]

    return cache


def increment_exchange(cache: dict) -> int:
    """Increment the stop-event counter. Returns the new count."""
    count = cache.get("exchange_count", 0) + 1
    cache["exchange_count"] = count
    return count


def reset_exchange_count(cache: dict) -> dict:
    """Reset counter after auto_store fires."""
    cache["exchange_count"] = 0
    return cache


def add_stored_hashes(cache: dict, hashes: list[str]) -> dict:
    """Append newly stored content hashes for cross-run dedup."""
    stored = cache.setdefault("stored_content_hashes", [])
    stored.extend(hashes)
    if len(stored) > _MAX_STORED_HASHES:
        cache["stored_content_hashes"] = stored[-_MAX_STORED_HASHES:]
    return cache


def get_stored_hashes(cache: dict) -> set[str]:
    return set(cache.get("stored_content_hashes", []))
