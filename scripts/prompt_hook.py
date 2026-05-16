"""
UserPromptSubmit hook for Engram.

Wired into ~/.claude/settings.json UserPromptSubmit hook.
Fires before every Claude Code prompt across all projects.

Retrieves relevant memories from Engram and injects them as a systemMessage
so Claude has long-term context before processing the user's request.

Design:
- Enriches the query with the detected project name for better graph traversal
- Session deduplication: skips retrieval if identical query seen this session
- Filters chunk_ids already returned this session to avoid redundant injection
- Gracefully degrades on any error (services down, import failure, etc.)

Exit codes:
  0 -- hook completed (session continues normally)

Output JSON (when memories found):
  {"systemMessage": "[Engram context]\n[type] content (project, session N)\n..."}
"""

import asyncio
import hashlib
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


_SKIP_NAMES = {"projects", "src", "code", "work", "dev", "home", "users"}


def _detect_project(cwd: str) -> str | None:
    """Infer project name from working directory basename."""
    if not cwd:
        return None
    p = Path(cwd)
    name = p.name
    if name.lower() not in _SKIP_NAMES:
        # Guard: don't return a bare username (C:/Users/Kevin)
        if p.parent.name.lower() == "users":
            return None
        return name if name else None
    # Try one level up, but guard against climbing into a username
    # (e.g. C:/Users/Kevin/Projects -> parent="Kevin", grandparent="Users" -> skip)
    parent_path = p.parent
    parent = parent_path.name
    grandparent = parent_path.parent.name
    if grandparent.lower() == "users":
        return None
    if parent.lower() not in _SKIP_NAMES and parent:
        return parent
    return None


def _query_hash(query: str) -> str:
    return hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]


def _format_age(timestamp: str | None) -> str:
    if not timestamp:
        return "?"
    try:
        ts = datetime.fromisoformat(timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        days = delta.days
        if days == 0:
            return "today"
        if days == 1:
            return "1d ago"
        return f"{days}d ago"
    except Exception:
        return "?"


def _format_memories(memories: list[dict]) -> str:
    lines = ["[Engram context]"]
    for m in memories:
        mtype = m.get("memory_type", "memory")
        content = m.get("content", "").strip().replace("\n", " ")
        if len(content) > 120:
            content = content[:117] + "..."
        project = m.get("project") or ""
        age = _format_age(m.get("timestamp"))
        meta = ", ".join(filter(None, [project, age]))
        lines.append(f"[{mtype}] {content} ({meta})")
    return "\n".join(lines)


async def _retrieve(query: str, project: str | None, limit: int) -> list[dict]:
    from engram_mcp.tools.retrieve import retrieve_context
    return await retrieve_context(query=query, limit=limit, project=project)


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return

    prompt = data.get("prompt", "").strip()
    cwd = data.get("cwd", "")
    session_id = data.get("session_id", "")

    if not prompt or not session_id:
        return

    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from engram_config import load_config
        config = load_config(cwd)
    except Exception:
        config = {"auto_retrieve": True, "retrieve_limit": 5}

    if not config.get("auto_retrieve", True):
        return

    project = _detect_project(cwd)
    enriched_query = f"In project {project}: {prompt}" if project else prompt
    q_hash = _query_hash(enriched_query)

    try:
        from scripts.session_cache import load_cache, save_cache, has_seen_query, record_query
    except ImportError:
        sys.path.insert(0, str(ROOT / "scripts"))
        from session_cache import load_cache, save_cache, has_seen_query, record_query

    cache = load_cache(session_id)

    # Save cwd so stop_hook can load per-project config without receiving cwd itself
    if cwd:
        cache["cwd"] = cwd

    if has_seen_query(cache, q_hash):
        save_cache(session_id, cache)
        return  # identical query this session — skip

    try:
        memories = asyncio.run(_retrieve(enriched_query, project, config.get("retrieve_limit", 5)))
    except Exception:
        return

    if not memories:
        return

    # Filter out chunk_ids already surfaced this session
    seen = set(cache.get("seen_chunk_ids", []))
    fresh = [m for m in memories if m.get("chunk_id") not in seen]

    if not fresh:
        return

    cache = record_query(cache, q_hash, [m["chunk_id"] for m in fresh])
    save_cache(session_id, cache)

    print(json.dumps({"systemMessage": _format_memories(fresh)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
