"""
Stop hook for Engram — throttled launcher.

Wired into ~/.claude/settings.json Stop hook.
Fires after every Claude Code response.

Spawns auto_store.py as a detached background process every ENGRAM_EXCHANGE_THRESHOLD
responses (default 5). This gives subconscious mid-session storage without making a
Haiku API call on every single turn.

auto_store.py processes only new transcript messages since its last run (incremental),
so each invocation is cheap and deduplication is handled via session cache.

Exit codes:
  0 -- hook completed (session continues normally)
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    if data.get("stop_hook_active"):
        return

    transcript_path = data.get("transcript_path", "")
    session_id = data.get("session_id", "")

    if not transcript_path or not session_id:
        return

    auto_store = ROOT / "scripts" / "auto_store.py"
    if not auto_store.exists():
        return

    try:
        from scripts.session_cache import (
            load_cache, save_cache, increment_exchange, reset_exchange_count,
        )
    except ImportError:
        sys.path.insert(0, str(ROOT / "scripts"))
        from session_cache import (
            load_cache, save_cache, increment_exchange, reset_exchange_count,
        )

    cache = load_cache(session_id)
    cache["transcript_path"] = transcript_path  # compact_hook reads this

    # Load per-project config using cwd saved by prompt_hook (which has cwd from stdin)
    cwd = cache.get("cwd", "")
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from engram_config import load_config
        config = load_config(cwd)
    except Exception:
        config = {"auto_store": True, "exchange_threshold": 5}

    if not config.get("auto_store", True):
        save_cache(session_id, cache)
        return

    exchange_count = increment_exchange(cache)
    threshold = config.get("exchange_threshold", 5)

    should_run = exchange_count >= threshold
    if should_run:
        reset_exchange_count(cache)

    save_cache(session_id, cache)

    if not should_run:
        return

    try:
        subprocess.Popen(
            [
                sys.executable,
                str(auto_store),
                "--transcript", transcript_path,
                "--session", session_id,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(ROOT),
            start_new_session=True,
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
