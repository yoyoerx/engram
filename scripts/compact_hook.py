"""
PreCompact hook for Engram.

Wired into ~/.claude/settings.json PreCompact hook.
Fires before Claude Code compacts the conversation context.

Two responsibilities:

1. Spawn auto_store.py — compaction is the highest-value moment to extract memories
   because accumulated context is about to be lost. We fire unconditionally here
   (no exchange threshold) to capture everything before it disappears.

2. Clear seen_chunk_ids from the session cache so the next UserPromptSubmit
   re-injects relevant context into the fresh post-compaction window.
   Query hashes are preserved — no point re-running identical queries,
   but the results of those queries need to be eligible for re-injection.

Exit codes:
  0 -- hook completed (compaction proceeds normally)
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return

    session_id = data.get("session_id", "")
    if not session_id:
        return

    try:
        from scripts.session_cache import load_cache, save_cache
    except ImportError:
        sys.path.insert(0, str(ROOT / "scripts"))
        from session_cache import load_cache, save_cache

    cache = load_cache(session_id)

    # Spawn auto_store.py using the transcript_path saved by the last stop_hook run.
    # This captures everything accumulated since the last periodic store run.
    transcript_path = cache.get("transcript_path", "")
    auto_store = ROOT / "scripts" / "auto_store.py"
    if transcript_path and auto_store.exists():
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    str(auto_store),
                    "--transcript", transcript_path,
                    "--session", session_id,
                    "--force",  # bypass exchange-count threshold on compaction
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(ROOT),
                start_new_session=True,
            )
        except Exception:
            pass

    # Clear dedup state so memories are re-injected into the post-compaction window.
    cache["seen_chunk_ids"] = []
    save_cache(session_id, cache)


if __name__ == "__main__":
    main()
