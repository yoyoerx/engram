"""
Stop hook for Engram.

Wired into ~/.claude/settings.json Stop hook.
Fires after every Claude Code response. Checks when the last store_memory
call was made and injects a systemMessage nudging Claude to store if the
gap is large enough to risk losing context.

Thresholds:
  < 15 min  — silent (no output)
  15-30 min — mild reminder
  > 30 min  — strong nudge

Exit codes:
  0 — hook completed (session continues normally)

Output JSON:
  { "systemMessage": "..." }  or nothing (silent turns)
"""

import json
import os
import sys
from datetime import datetime, timezone
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

        qdrant = QdrantClient(url=url, timeout=2)
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

        most_recent = max(timestamps)
        ts = datetime.fromisoformat(most_recent)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return int(delta.total_seconds() / 60)
    except Exception:
        return None


def main() -> None:
    try:
        raw = sys.stdin.read()
        _data = json.loads(raw) if raw.strip() else {}
    except Exception:
        _data = {}

    minutes = _minutes_since_last_store()

    if minutes is None or minutes < 15:
        # Silent — Qdrant unreachable, or a recent store already happened
        return

    if minutes > 30:
        msg = (
            f"[Engram] {minutes} minutes since last store_memory. "
            f"Call mcp__engram__store_memory now if any decisions, feedback, "
            f"bugs, or task completions from this session have not been saved."
        )
    else:
        msg = (
            f"[Engram] {minutes} minutes since last store_memory. "
            f"Consider calling mcp__engram__store_memory if anything worth "
            f"keeping came up this turn."
        )

    print(json.dumps({"systemMessage": msg}, ensure_ascii=True))


if __name__ == "__main__":
    main()
