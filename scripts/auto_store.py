"""
Auto-store background agent for Engram.

Spawned by stop_hook.py (every N exchanges) and compact_hook.py (on compaction).
Reads only the NEW portion of the session transcript since the last run,
calls Claude Haiku to extract memorable facts, then stores them via store_memory.

Incremental: tracks last processed message count in session cache.
Cross-run dedup: stored content hashes are persisted in session cache.

Usage:
  python scripts/auto_store.py --transcript /path/to/transcript.jsonl --session <id>
  python scripts/auto_store.py --transcript /path/to/transcript.jsonl --session <id> --force

  --force skips the minimum-new-messages guard (used by compact_hook at compaction time).

Logs to: ~/.engram/logs/auto_store.log
"""

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

_LOG_PATH = Path.home() / ".engram" / "logs" / "auto_store.log"
_MIN_NEW_MESSAGES = 4       # minimum new messages before bothering Haiku
_MAX_TRANSCRIPT_CHARS = 8000
_EXTRACT_MODEL = "claude-haiku-4-5-20251001"

_EXTRACTION_PROMPT = """\
You are a memory curator for a software development assistant.
Read this Claude Code session transcript and extract facts worth keeping \
in long-term memory: architectural decisions, bug patterns, user feedback \
or corrections, project milestones, and external references.

Ignore small talk, routine file reads, repetitive tool calls, and anything \
obviously derivable from the current codebase.

Return ONLY a JSON array. If nothing is worth storing, return [].
Each item must have these exact keys:
  "content"     -- the memory text (concise, 1-3 sentences)
  "memory_type" -- one of: decision, error, feedback, project, reference, user
  "project"     -- project name string or null

Example:
[
  {"content": "Atomic rollback pattern: if Qdrant fails after Neo4j write, DETACH DELETE the orphaned node.", "memory_type": "decision", "project": "engram"},
  {"content": "UserPromptSubmit hook must exit < 10s or Claude Code times it out.", "memory_type": "feedback", "project": null}
]

TRANSCRIPT:
"""


def _setup_logger() -> logging.Logger:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("auto_store")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
    return logger


def _parse_transcript(path: str) -> list[dict]:
    """Parse a Claude Code JSONL transcript into a list of message dicts."""
    messages = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                role = entry.get("role") or entry.get("type", "")
                content = entry.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    content = " ".join(text_parts)
                if role and content:
                    messages.append({"role": role, "content": str(content)})
            except Exception:
                continue
    except Exception:
        pass
    return messages


def _build_transcript_text(messages: list[dict]) -> str:
    """Concatenate messages into a single string, trimmed to _MAX_TRANSCRIPT_CHARS from the end."""
    parts = []
    for m in messages:
        role = m["role"].upper()
        content = m["content"].strip()
        parts.append(f"{role}: {content}")
    full = "\n\n".join(parts)
    if len(full) > _MAX_TRANSCRIPT_CHARS:
        full = full[-_MAX_TRANSCRIPT_CHARS:]
    return full


def _parse_haiku_response(text: str) -> list[dict]:
    """Extract a JSON array from Haiku's response, tolerating fences and trailing text."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = []
        for line in lines[1:]:
            if line.startswith("```"):
                break
            inner.append(line)
        text = "\n".join(inner).strip()
    start = text.find("[")
    if start == -1:
        return []
    result, _ = json.JSONDecoder().raw_decode(text, start)
    return result if isinstance(result, list) else []


def _call_haiku(transcript_text: str, log: logging.Logger | None = None) -> list[dict]:
    """Call Claude Haiku synchronously to extract memory candidates."""
    import anthropic
    import os

    sys.path.insert(0, str(ROOT))
    from engram_mcp.retry import call_with_retry_sync

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    msg = call_with_retry_sync(
        client.messages.create,
        model=_EXTRACT_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": _EXTRACTION_PROMPT + transcript_text}],
        max_attempts=3,
        base_delay=1.0,
        backoff=2.0,
    )
    raw_text = msg.content[0].text
    try:
        return _parse_haiku_response(raw_text)
    except Exception as exc:
        if log:
            log.error("_parse_haiku_response failed: %s | raw=%r", exc, raw_text[:200])
        raise


def _content_hash(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]


async def _store_candidates(
    candidates: list[dict],
    seen_hashes: set[str],
    log: logging.Logger,
) -> tuple[int, list[str]]:
    """Store candidates not already in seen_hashes. Returns (count, new_hashes)."""
    from engram_mcp.tools.store import store_memory

    stored = 0
    new_hashes: list[str] = []
    valid_types = {"decision", "error", "feedback", "project", "reference", "user"}

    for item in candidates:
        content = str(item.get("content", "")).strip()
        memory_type = str(item.get("memory_type", "project")).strip()
        project = item.get("project") or None

        if not content:
            continue

        h = _content_hash(content)
        if h in seen_hashes:
            log.info("skipping duplicate content hash %s", h)
            continue

        if memory_type not in valid_types:
            memory_type = "project"

        try:
            result = await store_memory(content=content, memory_type=memory_type, project=project)
            if result.get("stored"):
                stored += 1
                seen_hashes.add(h)
                new_hashes.append(h)
                log.info("stored %s (%s): %s...", memory_type, project or "global", content[:60])
            else:
                log.warning("store_memory returned stored=False: %s", result)
        except Exception as exc:
            log.error("store_memory failed: %s", exc)

    return stored, new_hashes


def main() -> None:
    log = _setup_logger()

    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--force", action="store_true",
                        help="bypass minimum-new-messages guard")
    args = parser.parse_args()

    log.info("auto_store started for session %s (force=%s)", args.session, args.force)

    try:
        from scripts.session_cache import (
            load_cache, save_cache, add_stored_hashes, get_stored_hashes,
        )
    except ImportError:
        sys.path.insert(0, str(ROOT / "scripts"))
        from session_cache import (
            load_cache, save_cache, add_stored_hashes, get_stored_hashes,
        )

    cache = load_cache(args.session)
    seen_hashes: set[str] = get_stored_hashes(cache)
    last_msg_count: int = cache.get("last_auto_store_msg_count", 0)

    all_messages = _parse_transcript(args.transcript)
    new_messages = all_messages[last_msg_count:]
    total_messages = len(all_messages)

    log.info(
        "transcript: %d total messages, %d new since last run (offset %d)",
        total_messages, len(new_messages), last_msg_count,
    )

    min_needed = 1 if args.force else _MIN_NEW_MESSAGES
    if len(new_messages) < min_needed:
        log.info("not enough new messages (%d < %d) — skipping", len(new_messages), min_needed)
        return

    transcript_text = _build_transcript_text(new_messages)

    try:
        candidates = _call_haiku(transcript_text, log)
    except Exception as exc:
        log.error("Haiku extraction failed: %s\n%s", exc, traceback.format_exc())
        return

    if not isinstance(candidates, list) or not candidates:
        log.info("no memory candidates extracted")
        # Still update the message count so we don't re-process this content
        cache = load_cache(args.session)
        cache["last_auto_store_msg_count"] = total_messages
        save_cache(args.session, cache)
        return

    log.info("extracted %d candidates from %d new messages", len(candidates), len(new_messages))

    stored_count, new_hashes = asyncio.run(_store_candidates(candidates, seen_hashes, log))

    # Reload cache to avoid overwriting concurrent stop_hook writes, then update
    cache = load_cache(args.session)
    cache["last_auto_store_msg_count"] = total_messages
    add_stored_hashes(cache, new_hashes)
    save_cache(args.session, cache)

    log.info(
        "auto_store complete: %d/%d candidates stored, %d new hashes cached",
        stored_count, len(candidates), len(new_hashes),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()} ERROR unhandled: {exc}\n")
        except Exception:
            pass
        sys.exit(0)
