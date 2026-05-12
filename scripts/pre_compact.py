"""
Pre-compaction hook script for Engram.

Called by the Claude Code PreCompact hook in ~/.claude/settings.json.
Receives session JSON on stdin (no conversation content — that is not provided
to PreCompact command hooks).

Outputs a JSON object with a systemMessage reminding Claude/the user to save
important memories before the context window is compacted. Optionally warns if
no memories have been stored in the current session (by checking Qdrant for
recent writes).

Exit codes:
  0 — hook completed (compaction proceeds normally)

Output JSON:
  { "systemMessage": "..." }
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _minutes_since_last_store() -> int | None:
    """Return minutes since the most recently stored memory, or None if unavailable."""
    try:
        from qdrant_client import QdrantClient

        url = os.getenv("QDRANT_URL", "http://localhost:6333")
        collection = os.getenv("QDRANT_COLLECTION", "engram_memories")

        qdrant = QdrantClient(url=url, timeout=3)
        # Fetch a recent batch and find the max timestamp (scroll is unordered).
        results, _ = qdrant.scroll(
            collection_name=collection,
            limit=100,
            with_payload=True,
            with_vectors=False,
        )
        if not results:
            return None

        timestamps = [
            r.payload.get("timestamp", "")
            for r in results
            if r.payload and r.payload.get("timestamp")
        ]
        if not timestamps:
            return None

        most_recent = max(timestamps)  # ISO-8601 strings sort lexicographically
        ts = datetime.fromisoformat(most_recent)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return int(delta.total_seconds() / 60)
    except Exception:
        return None


def main() -> None:
    # Read hook input (session JSON) — ignore content, we just want to fire
    try:
        raw = sys.stdin.read()
        _data = json.loads(raw) if raw.strip() else {}
    except Exception:
        _data = {}

    minutes = _minutes_since_last_store()

    if minutes is None:
        # Could not reach Qdrant — services may be down
        msg = (
            "[Engram] Context compaction triggered. "
            "Engram could not be reached to check recent memory activity. "
            "If important decisions or feedback occurred this session, "
            "consider calling mcp__engram__store_memory before compaction completes."
        )
    elif minutes > 60:
        msg = (
            f"[Engram] Context compaction triggered. "
            f"No memories stored in the last {minutes} minutes. "
            f"Please call mcp__engram__store_memory for any key decisions, "
            f"feedback, or discoveries from this session before context is compressed."
        )
    elif minutes > 20:
        msg = (
            f"[Engram] Context compaction triggered. "
            f"Last memory stored {minutes} minutes ago. "
            f"Check whether anything important from this session still needs saving."
        )
    else:
        msg = (
            f"[Engram] Context compaction triggered. "
            f"Last memory stored {minutes} minutes ago — looks recent."
        )

    print(json.dumps({"systemMessage": msg}, ensure_ascii=True))


if __name__ == "__main__":
    main()
